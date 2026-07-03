#!/bin/bash
# set_minio_env.sh
# Apply environment variables for PlanApp development container

export MINIO_ENDPOINT="https://s3.ciseiplan.netiswork.pro.br"
export MINIO_ACCESS_KEY="admin"
export MINIO_SECRET_KEY="Inicio@123"
export MINIO_BUCKET="planapp"

export MINIO_HOME_FOLDER="home"
export LOCAL_HOME_FOLDER="/workspaces/planning_service/home"

export MINIO_DEM_ROOT_KEY="root/dem-datasets"

echo "MinIO environment variables applied."
