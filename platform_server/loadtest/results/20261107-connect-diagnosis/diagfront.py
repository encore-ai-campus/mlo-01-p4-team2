from config.service_asgi import application
from diag_hooks import install,wrap
install(False)
application=wrap(application)
