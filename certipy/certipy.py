###############################################################################
# Copyright (c) 2018, Lawrence Livermore National Security, LLC
# Produced at the Lawrence Livermore National Laboratory
# Written by Thomas Mendoza mendoza33@llnl.gov
# LLNL-CODE-754897
# All rights reserved
#
# This file is part of Certipy. For details, see
# https://github.com/LLNL/certipy. Please also read this link - Additional
# BSD Notice.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright notice,
#     this list of conditions and the disclaimer below.
#     * Redistributions in binary form must reproduce the above copyright
#     notice, this list of conditions and the disclaimer (as noted below) in
#     the documentation and/or other materials provided with the distribution.
#     * Neither the name of the LLNS/LLNL nor the names of its contributors may
#     be used to endorse or promote products derived from this software without
#     specific prior written permission.
#
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL LAWRENCE LIVERMORE NATIONAL SECURITY, LLC,
# THE U.S. DEPARTMENT OF ENERGY OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
#
# Additional BSD Notice
#
# 1. This notice is required to be provided under our contract with the U.S.
# Department of Energy (DOE). This work was produced at Lawrence Livermore
# National Laboratory under Contract No. DE-AC52-07NA27344 with the DOE.
#
# 2. Neither the United States Government nor Lawrence Livermore National
# Security, LLC nor any of their employees, makes any warranty, express or
# implied, or assumes any liability or responsibility for the accuracy,
# completeness, or usefulness of any information, apparatus, product,
# or process disclosed, or represents that its use would not infringe
# privately-owned rights.
#
# 3. Also, reference herein to any specific commercial products, process, or
# services by trade name, trademark, manufacturer or otherwise does not
# necessarily constitute or imply its endorsement, recommendation, or favoring
# by the United States Government or Lawrence Livermore National Security, LLC.
# The views and opinions of authors expressed herein do not necessarily state
# or reflect those of the United States Government or Lawrence Livermore
# National Security, LLC, and shall not be used for advertising or product
# endorsement purposes.
###############################################################################

import os
import json
import argparse
import logging
from enum import Enum
from collections import Counter
from OpenSSL import crypto
from contextlib import contextmanager


class TLSFileType(Enum):
    KEY = 'key'
    CERT = 'cert'
    CA = 'ca'


class CertNotFoundError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class CertExistsError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class CertificateAuthorityInUseError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


@contextmanager
def open_tls_file(file_path, mode, private=True):
    """Context to ensure correct file permissions for certs and directories

    Ensures:
        - A containing directory with appropriate permissions
        - Correct file permissions based on what the file is (0o600 for keys
        and 0o644 for certs)
    """

    containing_dir = os.path.dirname(file_path)
    fh = None
    try:
        if 'w' in mode:
            os.chmod(containing_dir, mode=0o755)
        fh = open(file_path, mode)
    except OSError as e:
        if 'w' in mode:
            os.makedirs(containing_dir, mode=0o755, exist_ok=True)
            os.chmod(containing_dir, mode=0o755)
            fh = open(file_path, 'w')
        else:
            raise
    yield fh
    mode = 0o600 if private else 0o644
    os.chmod(file_path, mode=mode)
    fh.close()


class TLSFile():
    """Describes basic information about files used for TLS"""

    def __init__(self, file_path, encoding=crypto.FILETYPE_PEM,
                 file_type=TLSFileType.CERT, x509=None):
        self.file_path = file_path
        self.containing_dir = os.path.dirname(self.file_path)
        self.encoding = encoding
        self.file_type = file_type
        self.x509 = x509

    def __str__(self):
        data = ''
        if not self.x509:
            return data

        if self.file_type is TLSFileType.KEY:
            data = crypto.dump_privatekey(
                self.encoding, self.x509).decode("utf-8")
        else:
            data = crypto.dump_certificate(
                self.encoding, self.x509).decode("utf-8")

        return data

    def is_private(self):
        """Is this a private key"""

        return True if self.file_type is TLSFileType.KEY else False

    def load(self):
        """Load from a file and return an x509 object"""

        private = self.is_private()
        with open_tls_file(self.file_path, 'r', private=private) as fh:
            if private:
                self.x509 = crypto.load_privatekey(self.encoding, fh.read())
            else:
                self.x509 = crypto.load_certificate(self.encoding, fh.read())
            return self.x509

    def save(self, x509):
        """Persist this x509 object to disk"""

        self.x509 = x509
        with open_tls_file(self.file_path, 'w',
                           private=self.is_private()) as fh:
            fh.write(str(self))


class TLSFileBundle():
    """Maintains information that is shared by a set of TLSFiles"""

    def __init__(self, common_name, files=None, x509s=None, serial=0,
                 is_ca=False, parent_ca='', signees=None):
        self.serial = serial
        self.parent_ca = parent_ca
        self.signees = signees
        for t in TLSFileType:
            setattr(self, t.value, None)

        files = files or {}
        x509s = x509s or {}
        self._setup_tls_files(files)
        self._save_x509s(x509s)

    def _setup_tls_files(self, files):
        """Initiates TLSFIle objects with the paths given to this bundle"""

        for file_type in TLSFileType:
            if file_type.value in files:
                file_path = files[file_type.value]
                setattr(self, file_type.value,
                        TLSFile(file_path, file_type=file_type))

    def _save_x509s(self, x509s):
        """Saves the x509 objects to the paths known by this bundle"""

        for file_type in TLSFileType:
            if file_type.value in x509s:
                x509 = x509s[file_type.value]
                if file_type is not TLSFileType.CA:
                    # persist this key or cert to disk
                    tlsfile = getattr(self, file_type.value)
                    if tlsfile:
                        tlsfile.save(x509)

    def load_all(self):
        """Utility to load bring all files into memory"""

        for t in TLSFileType:
            self[t.value].load()
        return self

    def is_ca(self):
        """Is this bundle for a CA certificate"""

        return bool(self.parent_ca)

    def to_record(self):
        """Create a CertStore record from this TLSFileBundle"""

        tf_list = [getattr(self, k, None) for k in
                   [_.value for _ in TLSFileType]]
        # If a cert isn't defined in this bundle, remove it
        tf_list = filter(lambda x: x, tf_list)
        return {
            'serial': self.serial,
            'parent_ca': self.parent_ca,
            'signees': self.signees,
            'files': {tf.file_type.value: tf.file_path for tf in tf_list},
        }

    def from_record(self, record):
        """Build a bundle from a CertStore record"""

        self.serial = record['serial']
        self.parent_ca = record['parent_ca']
        self.signees = record['signees']
        self._setup_tls_files(record['files'])
        return self


class CertStore():
    """Maintains records of certificates created by Certipy

    Minimally, each record keyed by common name needs:
        - file
            - path
            - type
        - serial number
        - parent CA
        - signees
    Common names, for the sake of simplicity, are assumed to be unique.
    If a pair of certs need to be valid for the same IP/DNS address (ex:
    localhost), that information can be specified in the Subject Alternative
    Name field.
    """

    def __init__(self, containing_dir='out', store_file='certipy.json'):
        self.store = {}
        self.containing_dir = containing_dir
        try:
            os.stat(containing_dir)
        except FileNotFoundError:
            os.makedirs(containing_dir, mode=0o755, exist_ok=True)
        finally:
            os.chmod(containing_dir, mode=0o755)
        self.store_file_path = os.path.join(containing_dir, store_file)

    def save(self):
        """Write the store dict to a file specified by store_file_path"""

        with open(self.store_file_path, 'w') as fh:
            fh.write(json.dumps(self.store, indent=4))

    def load(self):
        """Read the store dict from file"""

        with open(self.store_file_path, 'r') as fh:
            self.store = json.loads(fh.read())

    def get_record(self, common_name):
        """Return the record associated with this common name

        In most cases, all that's really needed to use an existing cert are
        the file paths to the files that make up that cert. This method
        returns just that and doesn't bother loading the associated files.
        """

        try:
            record = self.store[common_name]
            return record
        except KeyError as e:
            raise CertNotFoundError(
                "Unable to find record of {name}"
                .format(name=common_name), errors=e)

    def get_files(self, common_name):
        """Return a bundle of TLS files associated with a common name"""

        record = self.get_record(common_name)
        return TLSFileBundle(common_name).from_record(record)

    def add_record(self, common_name, serial=0, parent_ca='',
                   signees=None, files=None, record=None):
        """Manually create a record of certs

        Generally, Certipy can be left to manage certificate locations and
        storage, but it is occasionally useful to keep track of a set of
        certs that were created externally (for example, let's encrypt)
        """

        record = record or {
            'serial': serial,
            'parent_ca': parent_ca,
            'signees': signees,
            'files': files,
        }
        self.store[common_name] = record
        self.save()

    def add_files(self, common_name, x509s, files=None, parent_ca='',
                  signees=None, serial=0, overwrite=False):
        """Add a set files comprising a certificate to Certipy

        Used with all the defaults, Certipy will manage creation of file paths
        to be used to store these files to disk and automatically calls save
        on all TLSFiles that it creates (and where it makes sense to).
        """

        if common_name in self.store and not overwrite:
            raise CertExistsError(
                "Certificate {name} already exists!"
                " Set overwrite=True to force add."
                .format(name=common_name))
        elif common_name in self.store and overwrite:
            # TODO: update and bump serial
            pass
        else:
            file_base_tmpl = "{prefix}/{cn}/{cn}"
            file_base = file_base_tmpl.format(
                prefix=self.containing_dir, cn=common_name
            )
            try:
                ca_record = self.get_record(parent_ca)
                ca_file = ca_record['files']['cert']
            except CertNotFoundError:
                ca_file = ''
            files = files or {
                'key': file_base + '.key',
                'cert': file_base + '.crt',
                'ca': ca_file,
            }
            bundle = TLSFileBundle(
                common_name, files=files, x509s=x509s,
                serial=serial, parent_ca=parent_ca, signees=signees)
            self.store[common_name] = bundle.to_record()
        self.save()

    def add_sign_link(self, ca_name, signee_name):
        """Adds to the CA signees and a parent ref to the signee"""

        ca_record = self.get_record(ca_name)
        signee_record = self.get_record(signee_name)
        signees = ca_record['signees'] or {}
        signees = Counter(signees)
        if signee_name not in signees:
            signees[signee_name] = 1
            ca_record['signees'] = signees
            signee_record['parent_ca'] = ca_name
        self.save()

    def remove_sign_link(self, ca_name, signee_name):
        """Removes signee_name to the signee list for ca_name"""

        ca_record = self.get_record(ca_name)
        signee_record = self.get_record(signee_name)
        signees = ca_record['signees'] or {}
        signees = Counter(signees)
        if signee_name in signees:
            signees[signee_name] = 0
            ca_record['signees'] = signees
            signee_record['parent_ca'] = ''
        self.save()

    def update_record(self, common_name, **fields):
        """Update fields in an existing record"""

        record = self.get_record(common_name)
        if fields is not None:
            for field, value in fields:
                record[field] = value
        self.save()
        return record

    def remove_files(self, common_name):
        """Delete files and record associated with this common name"""

        bundle = self.get_files(common_name)
        num_signees = len(Counter(bundle.signees))
        if bundle.is_ca() and num_signees > 0:
            raise CertificateAuthorityInUseError(
                "Authority {name} has signed {x} certificates"
                .format(name=common_name, x=num_signees)
            )
        # TODO: delete the key and cert files
        del self.store[common_name]
        self.save()


class Certipy():
    def __init__(self, store_dir='out', store_file='certipy.json'):
        self.store = CertStore(containing_dir=store_dir, store_file=store_file)

    def create_key_pair(self, cert_type, bits):
        """
        Create a public/private key pair.

        Arguments: type - Key type, must be one of TYPE_RSA and TYPE_DSA
                   bits - Number of bits to use in the key
        Returns:   The public/private key pair in a PKey object
        """

        pkey = crypto.PKey()
        pkey.generate_key(cert_type, bits)
        return pkey

    def create_request(self, pkey, digest="sha256", **name):
        """
        Create a certificate request.

        Arguments: pkey   - The key to associate with the request
                   digest - Digestion method to use for signing, default is
                            sha256
                   exts   - X509 extensions see:
                            https://www.openssl.org/docs/manmaster/man5/
                            x509v3_config.html#STANDARD-EXTENSIONS
                            Dict in format:
                            key -> (val, critical)
                   **name - The name of the subject of the request, possible
                            arguments are:
                              C     - Country name
                              ST    - State or province name
                              L     - Locality name
                              O     - Organization name
                              OU    - Organizational unit name
                              CN    - Common name
                              emailAddress - E-mail address


        Returns:   The certificate request in an X509Req object
        """

        req = crypto.X509Req()
        subj = req.get_subject()

        if name is not None:
            for key, value in name.items():
                setattr(subj, key, value)

        req.set_pubkey(pkey)
        req.sign(pkey, digest)
        return req

    def sign(self, req, issuer_cert_key, validity_period, digest="sha256",
             extensions=None, serial=0):
        """
        Generate a certificate given a certificate request.

        Arguments: req         - Certificate request to use
                   issuer_cert - The certificate of the issuer
                   issuer_key  - The private key of the issuer
                   not_before  - Timestamp (relative to now) when the
                                 certificate starts being valid
                   not_after   - Timestamp (relative to now) when the
                                 certificate stops being valid
                   digest      - Digest method to use for signing,
                                 default is sha256
        Returns:   The signed certificate in an X509 object
        """

        issuer_cert, issuer_key = issuer_cert_key
        not_before, not_after = validity_period
        cert = crypto.X509()
        cert.set_serial_number(serial)
        cert.gmtime_adj_notBefore(not_before)
        cert.gmtime_adj_notAfter(not_after)
        cert.set_issuer(issuer_cert.get_subject())
        cert.set_subject(req.get_subject())
        cert.set_pubkey(req.get_pubkey())

        if extensions:
            for ext in extensions:
                if callable(ext):
                    ext = ext(cert)
                cert.add_extensions([ext])

        cert.sign(issuer_key, digest)

        return cert

    def create_ca_bundle_for_names(self, bundle_name, names):
        """Create a CA bundle to trust only certs defined in names
        """

        records = [rec for name, rec
                   in self.store.store.items() if name in names]
        return self.create_ca_bundle(
            bundle_name, ca_names=[r['parent_ca'] for r in records])

    def create_ca_bundle(self, bundle_name, ca_names=None):
        """
        Create a bundle of CA public certs for trust distribution

        Arguments: ca_names    - The names of CAs to include in the bundle
                   bundle_name - The name of the bundle file to output
        Returns:   Path to the bundle file
        """

        if not ca_names:
            ca_names = []
            for name, record in self.store.store.items():
                if not record['parent_ca']:
                    ca_names.append(name)

        out_file_path = os.path.join(self.store.containing_dir, bundle_name)
        with open(out_file_path, 'w') as fh:
            for name in ca_names:
                bundle = self.store.get_files(name)
                bundle.cert.load()
                fh.write(str(bundle.cert))
        return out_file_path

    def create_ca(self, name, cert_type=crypto.TYPE_RSA, bits=2048,
                  alt_names=b"", years=5, serial=0):
        """
        Create a self-signed certificate authority

        Arguments: name     - The name of the CA
                   cert_type - The type of the cert. TYPE_RSA or TYPE_DSA
                   bits     - The number of bits to use
                   alt_names - A byte string of alternative names for the CA
        Returns:   KeyCertPair for the new CA
        """

        cakey = self.create_key_pair(cert_type, bits)
        req = self.create_request(cakey, CN=name)
        extensions = [
            crypto.X509Extension(
                b"basicConstraints", True, b"CA:true, pathlen:0"),
            crypto.X509Extension(
                b"keyUsage", True, b"keyCertSign, cRLSign"),
            crypto.X509Extension(
                b"extendedKeyUsage", True, b"serverAuth, clientAuth"),
            lambda cert: crypto.X509Extension(
                b"subjectKeyIdentifier", False, b"hash", subject=cert),
            lambda cert: crypto.X509Extension(
                b"authorityKeyIdentifier", False, b"keyid:always",
                issuer=cert),
        ]

        if alt_names:
            extensions.append(
                crypto.X509Extension(b"subjectAltName", False, alt_names)
            )

        # TODO: start time before today for clock skew?
        cacert = self.sign(req, (req, cakey), (0, 60*60*24*365*years),
                           extensions=extensions)

        x509s = {'key': cakey, 'cert': cacert, 'ca': cacert}
        self.store.add_files(name, x509s)
        return self.store.get_record(name)

    def create_signed_pair(self, name, ca_name, cert_type=crypto.TYPE_RSA,
                           bits=2048, years=5, alt_names=b"", serial=0):
        """
        Create a key-cert pair

        Arguments: name     - The name of the key-cert pair
                   ca_name   - The name of the CA to sign this cert
                   cert_type - The type of the cert. TYPE_RSA or TYPE_DSA
                   bits     - The number of bits to use
                   alt_names - A byte string of alternative names for this cert
        Returns:   KeyCertPair for the new signed pair
        """

        key = self.create_key_pair(cert_type, bits)
        req = self.create_request(key, CN=name)
        extensions = [
            crypto.X509Extension(
                b"extendedKeyUsage", True, b"serverAuth, clientAuth"),
        ]

        if alt_names:
            extensions.append(
                crypto.X509Extension(b"subjectAltName", False, alt_names)
            )

        ca_bundle = self.store.get_files(ca_name)
        cacert = ca_bundle.cert.load()
        cakey = ca_bundle.key.load()

        cert = self.sign(req, (cacert, cakey), (0, 60*60*24*365*years),
                         extensions=extensions)

        x509s = {'key': key, 'cert': cert, 'ca': None}
        self.store.add_files(name, x509s, parent_ca=ca_name)

        # Relate these certs as being parent and child
        self.store.add_sign_link(ca_name, name)
        return self.store.get_record(name)
