#!/bin/bash
#SBATCH --job-name=rdrs_timezones
#SBATCH --exclude=cnicgiwscpu009 # seems broken right now
#SBATCH --ntasks=1
#SBATCH --time=12:00:00
#SBATCH --mem=12GB
#SBATCH --mail-user=wouter.knoben@usask.ca
#SBATCH --mail-type=ALL
#SBATCH --account=hpc_c_giws_clark
#SBATCH --output=//gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/7_forcing_data/slurm_logs/rdrs/7g_timezone/slurm-%A_%a.out
#SBATCH --array=1-1697 # completed 0 during testing

module load StdEnv/2020  gcc/9.3.0  openmpi/4.0.3 gdal/3.5.1 libspatialindex/1.8.5 geo-stack/2022a
source /gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/camels-spat-env/bin/activate
cd /gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/7_forcing_data
python 7g_update_time_index.py $SLURM_ARRAY_TASK_ID