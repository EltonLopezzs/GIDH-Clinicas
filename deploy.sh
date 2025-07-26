PROJECT_DIR="/var/www/clinica/GIDH-Clinicas"

 
cd $PROJECT_DIR
 
git pull
 
sudo systemctl restart clinica.service
 
 
echo "Deploy concluído em $(date)"

 