web: daphne hackabot.asgi:application --port $PORT --bind 0.0.0.0
worker: python manage.py hackabot_worker
release: python manage.py migrate
