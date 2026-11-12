"""Spawn-safe live-test process; import application models only after ASGI setup."""
from daphne.testing import DaphneProcess


def configure_recipe_server():
    from channels.testing.live import set_database_connection
    set_database_connection()
    from event_story import main
    main.window = lambda: {'phase':'open','preview':False,'end':'2026-11-11T22:00:00+09:00'}


class RecipeServerProcess(DaphneProcess):
    def __init__(self, *args, **kwargs):
        kwargs['setup'] = configure_recipe_server
        super().__init__(*args, **kwargs)
