source="esri_land_2023_extra"
destination="/media/einstein-bohr/ANIMA/Datasets"

#rsync -avP --remove-source-files $source $destination 

# First, run a 'Dry Run' to see exactly what will happen without moving anything
# rsync -avP --dry-run --remove-source-files $source $destination

# Once confirmed, run the real command
rsync -avP --remove-source-files $source $destination
