# Development rules for hackabot

## General rules

- NEVER create or run database migrations for me, I will do that myself manually.
- But you should go ahead and add/modify fields etc on Django models.
- NEVER try to install libraries or packages for me (e.g. pip install, brew install etc) just tell me that I need to do it
- Don't add useless comments saying what you changed, comments should be useful and add information (err on the side of not adding any comments at all)
- NEVER add docstrings to classes or functions
- All lines must be max 79 chars long
- Always format Python code with black
- In Python go for readable pythonic code, try to avoid using esoteric stuff like next() if possible
- ALWAYS try to use dict() over {} notation in Python unless the names of the keys are incompatible with dict() notation i.e. forbidden characters.
- NEVER add type hints in Python, I don't use type hints in Python at all (e.g. def func(text: str) -> str)
- Don't remove my comments, TODOs, print(), console.log() or empty lines at the end of the file
- There should always be an empty line at the end of the file
- In Python ALWAYS try to use double quotes for strings
- In Django views always serialize the returned models with to_dict() or a serializer function, never return a queryset or a list of values
- NEVER edit existing Django migrations.
- In Python, err on the side of not catching exceptions and letting them bubble up. NEVER add "except Exception:" or "except Exception as e:" or "except: " to a try/except block, always specify the exception type.
- In Python you don't need to create a __init__.py file to make a directory a package.

## Python imports

If you make changes to a file and one of it's imports is no longer referenced, ALWAYS remove it.

NEVER EVER put Python imports inline inside functions or classes or elsewhere, ALWAYS put them at the top of the file.

For example NEVER do this:

```python
def bla():
  from a import b
```

## Foreign keys in Django models

For foreign keys in Django models I always use string references e.g.

`models.ForeignKey("myapp.MyModel")`

or if in the same app:

`models.ForeignKey("MyModel")`

And NEVER a direct reference to the model class like this:

`models.ForeignKey(MyModel)`

## Coding style

- Match the existing coding style as closely as possible.
- This applies to Django models, views, serializers, etc.
- Look around the codebase for examples of how to do things.

## Testing

After making significant changes to the codebase, ALWAYS run the test suite:

```bash
uv run pytest
```

Coverage is checked automatically and tests will fail if coverage drops below
70%. To see which lines are missing coverage:

```bash
uv run pytest --cov-report=term-missing
```

When making changes to the codebase, keep the test suite up to date:

- If you add new functionality, add corresponding tests
- If you modify existing functionality, update the relevant tests
- If you fix a bug, consider adding a test that would have caught it
- Run the test suite before considering a task complete
- Ensure coverage stays above the 70% threshold

## Deploying

Deploy to production with the `/deploy` skill, which pushes to Heroku:

```bash
git push heroku main
```
