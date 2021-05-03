# Setup

- Install PostgreSQL
    ```
    sudo apt install postgresql postgresql-contrib libpq-dev
    ```
- Install Python packages
    ```
    pip3 install discord.py
    pip3 install psycopg2
    pip3 install PyYAML
    ```
- Create database and user
    ```
    sudo -i -u postgres
    psql
    
    CREATE USER admin WITH PASSWORD '<pass>';
    CREATE DATABASE "postal_pinger";
    GRANT ALL ON DATABASE "postal_pinger" TO admin;
    \q
    ```
- Copy `config.yml.template` to `config.yml` and configure it
- Copy `ppbot.service.template` to `ppbot.service` and configure it
- Copy `ppexportregs.service.template` to `ppexportregs.service` and configure it
- Setup bot as a service:
    ```
    sudo cp ppbot.service /etc/systemd/system/.
    sudo systemctl start ppbot
    sudo systemctl enable ppbot
  
    sudo cp ppexportregs.service /etc/systemd/system/.
    sudo systemctl start ppexportregs
    sudo systemctl enable ppexportregs
    ```
