"""Inspect archived local identities without authorizing transport or revalidating receipts."""
from datetime import datetime, timezone
from pathlib import Path


def inspect_archive(cfg):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization

    now = datetime.now(timezone.utc)
    ca = x509.load_pem_x509_certificate(Path(cfg['ca_file']).read_bytes())
    if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError('archive_ca_required')
    ca.verify_directly_issued_by(ca)
    if not ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign:
        raise ValueError('archive_ca_key_usage')

    def public_bytes(key):
        return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    def status(cert, party):
        state = ('not_yet_valid' if now < cert.not_valid_before_utc else
                 'expired' if now > cert.not_valid_after_utc else 'current')
        return dict(version=cfg['version'], party=party, fingerprint=cert.fingerprint(hashes.SHA256()).hex(),
                    not_before=cert.not_valid_before_utc.isoformat(), not_after=cert.not_valid_after_utc.isoformat(),
                    validity_at_restore=state)

    results = [status(ca, 'ca')]
    for side in ('a', 'b'):
        own = cfg[side]
        cert = x509.load_pem_x509_certificate(Path(own['cert_file']).read_bytes())
        cert.verify_directly_issued_by(ca)
        if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError('archive_leaf_certificate_required')
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        if not usage.digital_signature or not usage.key_encipherment:
            raise ValueError('archive_leaf_key_usage')
        if cert.fingerprint(hashes.SHA256()).hex() != own['fingerprint']:
            raise ValueError('archive_certificate_fingerprint_mismatch')
        raw = Path(own['key_file']).read_bytes()
        embedded = x509.load_pem_x509_certificate(raw)
        key = serialization.load_pem_private_key(raw, password=Path(own['password_file']).read_text('ascii').strip().encode())
        if (embedded.fingerprint(hashes.SHA256()) != cert.fingerprint(hashes.SHA256()) or
                public_bytes(key.public_key()) != public_bytes(cert.public_key())):
            raise ValueError('archive_key_certificate_mismatch')
        results.append({**status(cert, side), 'chain_signature_verified': True, 'private_key_matches': True})
    return results
