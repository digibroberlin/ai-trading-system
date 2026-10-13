FROM postgres:16
# Beim ALLERERSTEN Start mit leerem Datenvolumen wird nur die eingeschraenkte Anwendungsrolle angelegt.
# Das Schema entsteht ausschliesslich ueber die Migrationen:
#   docker-compose --env-file <Passwortdatei> run --rm migrate
COPY 00_roles.sh /docker-entrypoint-initdb.d/00_roles.sh
