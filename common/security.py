from cryptography.fernet import Fernet
from common.config import settings

# For skeleton, derive key from API_LOG_AUTH_TOKEN (do NOT do this in prod)
KEY = (settings.API_LOG_AUTH_TOKEN + "0"*32)[:32].encode("utf-8")
FERNET = Fernet(Fernet.generate_key())  # placeholder; replace with persisted key

def encrypt_password(p: str) -> str:
    return p  # stub

def decrypt_password(p: str) -> str:
    return p  # stub
