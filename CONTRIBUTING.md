# Contributing to nio-poll-bot

Thank you for taking interest in this project. Below is some information
to help you with contributing.

## Setting up your development environment

See the
[Install the dependencies section of SETUP.md](SETUP.md#install-the-dependencies)
for help setting up a running environment for the bot.

### Modifying dependencies

To add dependencies (See more info about [version constraints](https://python-poetry.org/docs/dependency-specification/)):
- Do `poetry add dependency^1.5.0` to add core dependencies.
- Do `poetry add --dev dependency^1.5.0` to add dev dependencies.
- Do `poetry lock` to lock the dependencies for deployment.

## Code style

Please follow the [PEP8](https://www.python.org/dev/peps/pep-0008/) style
guidelines and format your import statements with
[isort](https://pypi.org/project/isort/).

## Linting

Run the following script to automatically format your code. This *should* make
the linting CI happy:

```
./scripts-dev/lint.sh
```

## Releasing
* Update `CHANGELOG.md`
* Commit changelog
* Make a tag
* Push the tag
* Make a GitHub release, copy the changelog for the release there
* Build a docker image
  * `docker build -t murlock1000/nio_poll_bot:v<version> -f docker/Dockerfile --no-cache .`
  * `docker tag murlock1000/nio_poll_bot:v<version> murlock1000/nio_poll_bot:latest`
* Push docker images
* Consider announcing on `#thisweekinmatrix:matrix.org` \o/

## What to work on

Take a look at the [issues
list](https://github.com/murlock1000/nio-poll-bot/issues). What
feature would you like to see or bug do you want to be fixed?
