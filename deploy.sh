#!/bin/bash

 
PROJECT_DIR="/var/www/clinica/GIDH-Clinicas"

# Navega até o diretório do projeto
cd $PROJECT_DIR

# Puxa as últimas alterações do GitHub
git pull
 
sudo systemctl restart clinica.service
 
 
echo "Deploy concluído em $(date)"

 