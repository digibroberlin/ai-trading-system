FROM postgres:16
# Wird beim ALLERERSTEN Start mit leerem Datenvolumen ausgefuehrt (alphabetisch):
COPY 00_roles.sh /docker-entrypoint-initdb.d/00_roles.sh
COPY schema.sql  /docker-entrypoint-initdb.d/10_schema.sql
