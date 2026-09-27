from cryptography.fernet import Fernet, InvalidToken
import os


class EncryptionService:
    def __init__(self):
        self._fernet = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            key = os.getenv("FERNET_KEY")
            if not key:
                raise RuntimeError(
                    "FERNET_KEY environment variable not set. "
                    "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
            try:
                self._fernet = Fernet(key.encode())
            except ValueError:
                raise RuntimeError(
                    f"FERNET_KEY is not a valid Fernet key. It must be exactly 32 url-safe base64-encoded bytes. "
                    f"Generate a new one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        encrypted_bytes = self.fernet.encrypt(plaintext.encode())
        return encrypted_bytes.decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            decrypted_bytes = self.fernet.decrypt(ciphertext.encode())
            return decrypted_bytes.decode()
        except InvalidToken:
            raise ValueError("Failed to decrypt value - invalid or tampered data")


encryption_service = EncryptionService()
