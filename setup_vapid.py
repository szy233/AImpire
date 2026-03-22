#!/usr/bin/env python3
"""生成并打印 VAPID 密钥对"""
import base64, os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend

def url_safe_b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
public_key = private_key.public_key()

# 导出私钥（原始 32 bytes）
priv_bytes = private_key.private_numbers().private_value.to_bytes(32, 'big')
# 导出公钥（未压缩格式 04 + x + y，65 bytes）
pub_numbers = public_key.public_numbers()
pub_bytes = b'\x04' + pub_numbers.x.to_bytes(32, 'big') + pub_numbers.y.to_bytes(32, 'big')

print("VAPID_PUBLIC_KEY:", url_safe_b64(pub_bytes))
print("VAPID_PRIVATE_KEY:", url_safe_b64(priv_bytes))
