#!/bin/bash
#SBATCH --job-name=attr_1
#SBATCH --ntasks=1
#SBATCH --time=1:30:00
#SBATCH --mem=64GB
#SBATCH --mail-user=wouter.knoben@usask.ca
#SBATCH --mail-type=ALL
#SBATCH --account=hpc_c_giws_clark
#SBATCH --output=/gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/9_attributes/logs/run7/slurm-1a-%A_%a.out
#SBATCH --array=0-999

module load StdEnv/2020  gcc/9.3.0  openmpi/4.0.3 gdal/3.5.1 libspatialindex/1.8.5 geo-stack/2022a
source /gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/camels-spat-env/bin/activate
cd /gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/9_attributes
python 1_calculate_attributes.py $SLURM_ARRAY_TASK_ID > ./logs/run7/terminal_{$SLURM_ARRAY_TASK_ID}.out

