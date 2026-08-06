#!/bin/bash
# set_minio_env.sh
# Apply environment variables for PlanApp development container

export MINIO_ENDPOINT="https://s3.ciseiplan.netiswork.pro.br"
export MINIO_ACCESS_KEY="admin"
export MINIO_SECRET_KEY="Inicio@123"
export MINIO_BUCKET="planapp"

export MINIO_HOME_FOLDER="home"
export LOCAL_HOME_FOLDER="/workspaces/network_service/home"

export MINIO_DEM_ROOT_KEY="root/dem-datasets"

# export MS_LINK_FEATURES="http://192.168.100.29:8080"
# export MS_LINK_FEATURES="http://10.32.13.19:8080"
export MS_LINK_FEATURES="http://planning-service:8080"

echo "MinIO environment variables applied."
