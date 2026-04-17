python -c "from db import init_db; init_db()" && gunicorn app:app
