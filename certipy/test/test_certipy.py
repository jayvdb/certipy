###############################################################################
# Copyright (c) 2018, Lawrence Livermore National Security, LLC
# Produced at the Lawrence Livermore National Laboratory
# Written by Thomas Mendoza mendoza33@llnl.gov
# LLNL-CODE-754897
# All rights reserved
#  
# This file is part of Certipy. For details, see https://github.com/LLNL/certipy.
# Please also read this link - Additional BSD Notice.
#  
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#  
#     * Redistributions of source code must retain the above copyright notice,
#     this list of conditions and the disclaimer below.  
#     * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions and the disclaimer (as noted below) in the
#     documentation and/or other materials provided with the distribution.  
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
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
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
# The views and opinions of authors expressed herein do not necessarily state or
# reflect those of the United States Government or Lawrence Livermore National
# Security, LLC, and shall not be used for advertising or product endorsement
# purposes.
###############################################################################

import os
import pytest
import shutil
from pytest import fixture
from OpenSSL import crypto
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from ..certipy import (
   TLSFileType, TLSFile, TLSFileBundle, CertStore, open_tls_file,
   CertExistsError, Certipy
)

@fixture(scope='module')
def signed_key_pair():
    pkey = crypto.PKey()
    pkey.generate_key(crypto.TYPE_RSA, 2048)
    req = crypto.X509Req()
    subj = req.get_subject()

    setattr(subj, 'CN', 'test')

    req.set_pubkey(pkey)
    req.sign(pkey, 'sha256')

    issuer_cert, issuer_key = (req, pkey)
    not_before, not_after = (0, 60*60*24*365*2)
    cert = crypto.X509()
    cert.set_serial_number(0)
    cert.gmtime_adj_notBefore(not_before)
    cert.gmtime_adj_notAfter(not_after)
    cert.set_issuer(issuer_cert.get_subject())
    cert.set_subject(req.get_subject())
    cert.set_pubkey(req.get_pubkey())

    cert.sign(issuer_key, 'sha256')
    return (pkey, cert)

@fixture(scope='module')
def record():
    return {
        'serial': 0,
        'parent_ca': '',
        'signees': None,
        'files': {
            'key': 'out/foo.key',
            'cert': 'out/foo.crt',
            'ca': 'out/ca.crt',
        },
    }


def test_tls_context_manager():
    def simple_perms(f):
        return oct(os.stat(f).st_mode & 0o777)

    # read
    with pytest.raises(OSError) as e:
        with open_tls_file('foo.test', 'r') as tlsfh:
            pass

    with NamedTemporaryFile('w') as fh:
        with open_tls_file(fh.name, 'r') as tlsfh:
            pass
    # write
    with NamedTemporaryFile('w') as fh:
        containing_dir = os.path.dirname(fh.name)
        # public certificate
        with open_tls_file(fh.name, 'w', private=False) as tlsfh:
            assert simple_perms(containing_dir) == '0o755'

        assert simple_perms(fh.name) == '0o644'

        # private certificate
        with open_tls_file(fh.name, 'w') as tlsfh:
            assert simple_perms(containing_dir) == '0o755'

        assert simple_perms(fh.name) == '0o600'


def test_tls_file(signed_key_pair):
    key, cert = signed_key_pair
    def read_write_key(file_type):
        with NamedTemporaryFile('w') as fh:
            tlsfile = TLSFile(fh.name, file_type=file_type)
            # test persist to disk
            x509 = cert if file_type is TLSFileType.CERT else key
            tlsfile.save(x509)
            with open(fh.name, 'r') as f:
                assert f.read() is not None
            # test load from disk
            loaded_tlsfile = TLSFile(fh.name, file_type=file_type)
            loaded_tlsfile.x509 = tlsfile.load()
            assert str(loaded_tlsfile) == str(tlsfile)

    # public key
    read_write_key(TLSFileType.CERT)

    # private key
    read_write_key(TLSFileType.KEY)

def test_tls_file_bundle(signed_key_pair, record):
    key, cert = signed_key_pair
    # from record
    bundle = TLSFileBundle('foo').from_record(record)
    assert bundle.key and bundle.cert and bundle.ca

    # to record
    exported_record = bundle.to_record()
    f_types = {key for key in exported_record['files'].keys()}
    assert len(f_types) == 3
    assert f_types == {'key', 'cert', 'ca'}

def test_certipy_store(signed_key_pair, record):
    key, cert = signed_key_pair
    key_str = crypto.dump_privatekey(crypto.FILETYPE_PEM, key)\
                .decode('utf-8')
    cert_str = crypto.dump_certificate(crypto.FILETYPE_PEM, cert)\
                .decode('utf-8')
    with TemporaryDirectory() as td:
        common_name = 'foo'
        store = CertStore(containing_dir=td)
        # add files
        x509s = {
            'key': key,
            'cert': cert,
            'ca': None,
        }
        store.add_files(common_name, x509s)

        # check the TLSFiles
        bundle = store.get_files(common_name)
        bundle.key.load()
        bundle.cert.load()
        assert key_str == str(bundle.key)
        assert cert_str == str(bundle.cert)

        # save the store records to a file
        store.save()

        # read the records back in
        store.load()

        # check the record for those files
        main_record = store.get_record(common_name)
        non_empty_paths = [f for f in main_record['files'].values() if f]
        assert len(non_empty_paths) == 2

        # add another record with no physical files
        signee_common_name = 'bar'
        store.add_record(signee_common_name, record=record)

        # 'sign' cert
        store.add_sign_link(common_name, signee_common_name)
        signee_record = store.get_record(signee_common_name)
        assert len(main_record['signees']) == 1
        assert signee_record['parent_ca'] == common_name

def test_certipy():
    with TemporaryDirectory() as td:
        # create a CA
        ca_name = 'foo'
        certipy = Certipy(store_dir=td)
        ca_record = certipy.create_ca(ca_name)

        non_empty_paths = [f for f in ca_record['files'].values() if f]
        assert len(non_empty_paths) == 2

        # check that the paths are backed by actual files
        ca_bundle = certipy.store.get_files(ca_name)
        assert ca_bundle.key.load() is not None
        assert ca_bundle.cert.load() is not None
        assert 'PRIVATE' in str(ca_bundle.key)
        assert 'CERTIFICATE' in str(ca_bundle.cert)

        # create a cert and sign it with that CA
        cert_name = 'bar'
        cert_record = certipy.create_signed_pair(cert_name, ca_name)

        non_empty_paths = [f for f in cert_record['files'].values() if f]
        assert len(non_empty_paths) == 3
        assert cert_record['files']['ca'] == ca_record['files']['cert']
