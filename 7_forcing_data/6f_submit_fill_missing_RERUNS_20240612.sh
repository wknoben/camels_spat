#!/bin/bash
#SBATCH --job-name=rr_daymet_var_fill
#SBATCH --ntasks=1
#SBATCH --time=12:00:00
#SBATCH --mem=200GB
#SBATCH --mail-user=wouter.knoben@usask.ca
#SBATCH --mail-type=ALL
#SBATCH --account=hpc_c_giws_mem_clark
#SBATCH --output=//gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/7_forcing_data/slurm_logs/daymet/6f_var_fill/slurm-%A_%a.out
#SBATCH --array=956,963,985,986

module load StdEnv/2020  gcc/9.3.0  openmpi/4.0.3 gdal/3.5.1 libspatialindex/1.8.5 geo-stack/2022a
source /gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/camels-spat-env/bin/activate
cd /gpfs/mdiops/globalhome/wmk934/HPC/camels_spat/7_forcing_data
python 6f_add_missing_days.py $SLURM_ARRAY_TASK_ID
