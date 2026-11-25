#!/bin/sh
# Runs once, when the data volume is first made: a database the tests may empty.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -c "CREATE DATABASE tripmate_test"
