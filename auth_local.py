"""Local browser sessions: service credentials stay on the server."""
import hashlib
import hmac
import os
import time

def session_value():
    stamp = str(int(time.time()))
    signature = hmac.new(os.environ['FUSION_API_TOKEN'].encode(), stamp.encode(), hashlib.sha256).hexdigest()
    return stamp + '.' + signature

def valid_session(value):
    try:
        stamp, signature = value.split('.')
        age = time.time() - int(stamp)
        expected = hmac.new(os.environ['FUSION_API_TOKEN'].encode(), stamp.encode(), hashlib.sha256).hexdigest()
        return 0 <= age <= 28800 and hmac.compare_digest(signature, expected)
    except (ValueError, KeyError, AttributeError):
        return False
