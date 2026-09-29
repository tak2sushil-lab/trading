"""RFC 6238 TOTP (the Google Authenticator algorithm): 6 digits, 30 s, SHA-1. Stdlib only."""
import base64, hashlib, hmac, struct


def totp_code(secret_b32, counter):
    key = base64.b32decode(secret_b32 + '=' * (-len(secret_b32) % 8))
    h = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return '%06d' % ((struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000)
