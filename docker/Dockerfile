# To build the image, run `docker build` command from the root of the
# repository:
#
#    docker build -f docker/Dockerfile .
#
# There is an optional PYTHON_VERSION build argument which sets the
# version of python to build against. For example:
#
#    docker build -f docker/Dockerfile --build-arg PYTHON_VERSION=3.10 .
#
# An optional LIBOLM_VERSION build argument which sets the
# version of libolm to build against. For example:
#
#    docker build -f docker/Dockerfile --build-arg LIBOLM_VERSION=3.2.10 .
#


##
## Creating a builder container
##

# We use an initial docker container to build all of the runtime dependencies,
# then transfer those dependencies to the container we're going to ship,
# before throwing this one away
ARG PYTHON_VERSION=3.8
FROM docker.io/python:${PYTHON_VERSION}-alpine as builder

##
## Build libolm for matrix-nio e2e support
##

# Install libolm build dependencies
ARG LIBOLM_VERSION=3.2.16
#--no-cache
RUN apk add \
    make \
    cmake \
    gcc \
    g++ \
    git \
    libffi-dev \
    yaml-dev \
    python3-dev \
    postgresql-dev \
    musl-dev

# Build libolm
#
# Also build the libolm python bindings and place them at /python-libs
# We will later copy contents from both of these folders to the runtime
# container
COPY docker/build_and_install_libolm.sh /scripts/
RUN /scripts/build_and_install_libolm.sh ${LIBOLM_VERSION} /python-libs

# Install python runtime modules. We do this before copying the source code
# such that these dependencies can be cached
# This speeds up subsequent image builds when the source code is changed
RUN mkdir -p /app/nio_poll_bot
COPY nio_poll_bot/__init__.py /app/nio_poll_bot/
COPY README.md main.py /app/

# Build the dependencies
RUN pip install --upgrade pip
# Pin poetry version so updates don't break the build
RUN pip install poetry==1.8.2

# Disable poetry venv creation in builder.
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=0

WORKDIR /app
COPY pyproject.toml poetry.lock /app/

# Export poetry.lock to requirements file omitting hashes for faster dependency resolution
# And building libraries in external folder /python-libs
RUN poetry export --without-hashes --without dev --format=requirements.txt > requirements.txt
# Install production dependencies
RUN pip install --prefix="/python-libs" --ignore-installed --no-warn-script-location -r requirements.txt

# Now copy the source code
COPY *.py *.md /app/
COPY nio_poll_bot/*.py /app/nio_poll_bot/

##
## Creating the runtime container
##

# Create the container we'll actually ship. We need to copy libolm and any
# python dependencies that we built above to this container
FROM docker.io/python:${PYTHON_VERSION}-alpine

# Copy python dependencies from the "builder" container
COPY --from=builder /python-libs /usr/local

# Copy libolm from the "builder" container
COPY --from=builder /usr/local/lib/libolm* /usr/local/lib/

# Install any native runtime dependencies
RUN apk add --no-cache \
    libstdc++ 

RUN apk add --no-cache \
    libpq

# Copy app files
COPY *.py *.md /app/
COPY nio_poll_bot/*.py /app/nio_poll_bot/

# Specify a volume that holds the config file, SQLite3 database,
# and the matrix-nio store
VOLUME ["/data"]

# Start the bot
ENTRYPOINT ["python", "/app/main.py", "/data/config.yaml"]
