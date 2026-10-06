import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.engine import get_session_factory
from src.db.models import User
from src.users.service import hash_password
from sqlalchemy import select

def reset_password(login, new_password):
    db = get_session_factory()()
    try:
        user = db.scalar(select(User).where(User.login == login))
        if user:
            user.password_hash = hash_password(new_password)
            db.commit()
            print(f"Пароль для пользователя {login} успешно изменен.")
        else:
            print(f"Пользователь {login} не найден.")
    finally:
        db.close()

if __name__ == "__main__":
    reset_password("demo_admin", "Admin123456")
