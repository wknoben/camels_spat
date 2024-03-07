import baseflow
import geopandas as gpd
import glob
import numpy as np
import os
import pandas as pd
import rasterio
import xarray as xr
from dateutil.relativedelta import relativedelta
from osgeo import gdal, osr
from rasterstats import zonal_stats
from scipy.stats import circmean, circstd, skew, kurtosis

## ------- Collection functions
def attributes_from_glhymps(geo_folder, dataset, l_values, l_index):

    '''Calculates attributes from GLHYMPS'''

    # Load the file
    geol_str = str(geo_folder / dataset / 'raw' / 'glhymps.shp')
    geol = gpd.read_file(geol_str)

    # Rename the columns, because the shortened ones are not very helpful
    if ('Porosity' in geol.columns) and ('Permeabili' in geol.columns) \
    and ('Permeabi_1' in geol.columns) and ('Permeabi_2' in geol.columns):
        geol.rename(columns={'Porosity': 'porosity',   'Permeabili': 'logK_Ferr',
                             'Permeabi_1': 'logK_Ice', 'Permeabi_2': 'logK_std'}, inplace=True)
        geol.to_file(geol_str)

    # Porosity
    l_values.append( geol['porosity'].min() )
    l_index.append(('Geology', 'porosity_min',  '-', 'GLHYMPS'))
    l_values.append( geol['porosity'].mean() )
    l_index.append(('Geology', 'porosity_mean',  '-', 'GLHYMPS'))
    l_values.append( geol['porosity'].max() )
    l_index.append(('Geology', 'porosity_max',  '-', 'GLHYMPS'))
    l_values.append( geol['porosity'].std() )
    l_index.append(('Geology', 'porosity_std',  '-', 'GLHYMPS'))

    # Permeability
    l_values.append( geol['logK_Ice'].min() )
    l_index.append(('Geology', 'log_permeability_min',  'm^2', 'GLHYMPS'))
    l_values.append( geol['logK_Ice'].mean() )
    l_index.append(('Geology', 'log_permeability_mean',  'm^2', 'GLHYMPS'))
    l_values.append( geol['logK_Ice'].max() )
    l_index.append(('Geology', 'log_permeability_max',  'm^2', 'GLHYMPS'))
    l_values.append( geol['logK_Ice'].std() )
    l_index.append(('Geology', 'log_permeability_std',  'm^2', 'GLHYMPS'))
    
    return l_values, l_index

def attributes_from_pelletier(geo_folder, dataset, shp_str, l_values, l_index):
    
    '''Calculates statistics for Pelletier maps'''
    # See: https://daac.ornl.gov/SOILS/guides/Global_Soil_Regolith_Sediment.html

    # General
    files = ['upland_hill-slope_regolith_thickness.tif',
             'upland_hill-slope_soil_thickness.tif',
             'upland_valley-bottom_and_lowland_sedimentary_deposit_thickness.tif',
             'average_soil_and_sedimentary-deposit_thickness.tif']
    attrs = ['regolith_thickness',    'soil_thickness',
             'sedimentary_thickness', 'average_thickness']
    units = ['m', 'm', 'm', 'm']
    stats = ['mean', 'min', 'max', 'std']

    for file,att,unit in zip(files,attrs,units):
        tif = str( geo_folder / dataset / 'raw' / file )
        zonal_out = zonal_stats(shp_str, tif, stats=stats)
        scale,offset = read_scale_and_offset(tif)
        l_values = update_values_list(l_values, stats, zonal_out, scale, offset)
        l_index += [('Soil', f'{att}_min',  f'{unit}', 'Pelletier'),
                    ('Soil', f'{att}_mean', f'{unit}', 'Pelletier'),
                    ('Soil', f'{att}_max',  f'{unit}', 'Pelletier'),
                    ('Soil', f'{att}_std',  f'{unit}', 'Pelletier')]
        
    return l_values,l_index

def attributes_from_streamflow(hyd_folder, dataset, basin_id, pre, row, l_values, l_index):
    
    '''Calculates various streamflow signatures'''
    
    # Constants
    seconds_per_minute = 60 # s min-1
    seconds_per_hour = 60 * seconds_per_minute # s hr-1
    seconds_per_day = 24 * seconds_per_hour # s d-1
    water_density = 1000 # kg m-3
    mm_per_m = 1000 # mm m-1
    m_per_km = 1000 # m km-1
    
    # Find the data source
    if row['Country'] == 'CAN':
        source = 'WSC'
    elif row['Country'] == 'USA':
        source = 'USGS'
    
    # Load observations
    hyd_file = hyd_folder / f'{basin_id}_daily_flow_observations.nc'
    hyd = xr.open_dataset(hyd_file)
    hyd = subset_dataset_to_max_full_years(hyd, res='day', water_year=True)
    
    # Convert observations to mm d-1
    area_km2 = row['Basin_area_km2']
    area_m2 = area_km2 * m_per_km**2 # [km2] * ([m km-1]^2 = [m2 km-2]) = [m2]
    hyd['q_obs'] = hyd['q_obs'] * seconds_per_day / area_m2 * mm_per_m # [m3 s-1] * [s d-1] / [m2] * [mm m-1] = [m d-1]
    
    # Convert precipitation into mm d-1
    pre = (pre * seconds_per_hour).resample(time='1D').sum() / water_density * mm_per_m # ([kg m-2 s-1] * [s hr-1]).resample(time='1D').sum() / [kg m-3] * [mm m-1] = [mm d-1]
    
    # Match times between hydrologic data and precipitation
    pre = pre.sel(time=slice(hyd['time'][0].values, hyd['time'][-1].values))
    assert hyd['time'][0].values == pre['time'][0].values, 'attributes_from_streamflow(): mismatch between precipitation and streamflow start timestamp'
    assert hyd['time'][-1].values == pre['time'][-1].values, 'attributes_from_streamflow(): mismatch between precipitation and streamflow final timestamp'
    
    # Create a water-year time variable
    hyd['water_year'] = hyd['time'].dt.year.where(hyd['time'].dt.month < 10, hyd['time'].dt.year + 1)
    pre['water_year'] = pre['time'].dt.year.where(pre['time'].dt.month < 10, pre['time'].dt.year + 1)
    
    # Track the data years used
    num_years = len(hyd.groupby('time.year'))
    l_values.append(num_years)
    l_index.append( ('Hydrology', 'num_years_hyd ', 'years', '-') )
    
    # Signatures
    calculate_signatures(hyd, pre, source, l_values, l_index)
    
    return l_values, l_index

def attributes_from_hydrolakes(geo_folder, dataset, l_values, l_index):
    
    '''Calculates open water attributes from HydroLAKES'''

    # Load the file
    lake_str = str(geo_folder / dataset / 'raw'    / 'HydroLAKES_polys_v10_NorthAmerica.shp')
    lakes = gpd.read_file(lake_str)

    # General stats
    res_mask  = lakes['Lake_type'] == 2 # Lake Type 2 == reservoir; see docs (https://data.hydrosheds.org/file/technical-documentation/HydroLAKES_TechDoc_v10.pdf)
    num_lakes = len(lakes)
    num_resvr = res_mask.sum() 
    l_values.append(num_lakes)
    l_index.append(('Open water', 'open_water_number',  '-', 'HydroLAKES'))
    l_values.append(num_resvr)
    l_index.append(('Open water', 'known_reservoirs',  '-', 'HydroLAKES'))

    # Summary stats
    l_values, l_index = get_open_water_stats(lakes, 'Lake_area', 'all', l_values, l_index) # All open water
    l_values, l_index = get_open_water_stats(lakes, 'Vol_total', 'all', l_values, l_index)
    l_values, l_index = get_open_water_stats(lakes, 'Lake_area', 'reservoir', l_values, l_index) # Reservoirs only
    l_values, l_index = get_open_water_stats(lakes, 'Vol_total', 'reservoir', l_values, l_index)

    return l_values, l_index

def attributes_from_merit(geo_folder, dataset, shp_str, riv_str, row, l_values, l_index):
    
    '''Calculates topographic attributes from MERIT data'''

    ## Known values
    lat = row['Station_lat']
    lon = row['Station_lon']
    area= row['Basin_area_km2']
    src = row['Station_source']
    l_values.append(lat)
    l_index.append(('Topography', 'gauge_lat',  'degrees', f'{src}'))
    l_values.append(lon)
    l_index.append(('Topography', 'gauge_lon',  'degrees', f'{src}'))
    l_values.append(area)
    l_index.append(('Topography', 'basin_area', 'km^2', 'MERIT Hydro'))

    ## RASTERS
    # Slope and elevation can use zonal stats
    files = [str(geo_folder / dataset / 'raw'    / 'merit_hydro_elv.tif'),
             str(geo_folder / dataset / 'slope'  / 'merit_hydro_slope.tif')]
    attrs = ['elev',     'slope']
    units = ['m.a.s.l.', 'degrees']
    stats = ['mean', 'min', 'max', 'std']
    for tif,att,unit in zip(files,attrs,units):
        zonal_out = zonal_stats(shp_str, tif, stats=stats)
        scale,offset = read_scale_and_offset(tif)
        l_values = update_values_list(l_values, stats, zonal_out, scale, offset)
        l_index += [('Topography', f'{att}_min',  f'{unit}', 'MERIT Hydro'),
                    ('Topography', f'{att}_mean', f'{unit}', 'MERIT Hydro'),
                    ('Topography', f'{att}_max',  f'{unit}', 'MERIT Hydro'),
                    ('Topography', f'{att}_std',  f'{unit}', 'MERIT Hydro')]

    # Aspect needs circular stats
    tif = str(geo_folder / dataset / 'aspect' / 'merit_hydro_aspect.tif')
    l_values, l_index = get_aspect_attributes(tif,l_values,l_index) 

    ## VECTOR
    l_values, l_index = get_river_attributes(riv_str, l_values, l_index, area)
    
    return l_values, l_index

def attributes_from_lgrip30(geo_folder, dataset, shp_str, l_values, l_index):

    '''Calculates percentage occurrence of all classes in LGRIP30 map'''

    tif = geo_folder / dataset / 'raw' / 'lgrip30_agriculture.tif'
    zonal_out = zonal_stats(shp_str, tif, categorical=True)
    check_scale_and_offset(tif)
    l_values,l_index = update_values_list_with_categorical(l_values, l_index, zonal_out, 'LGRIP30', prefix='lc3_')
    return l_values, l_index

def attributes_from_modis_land(geo_folder, dataset, shp_str, l_values, l_index):

    '''Calculates percentage occurrence of all classes in MODIS IGBP map'''

    tif = geo_folder / dataset / 'raw' / '2001_2022_mode_MCD12Q1_LC_Type1.tif'
    zonal_out = zonal_stats(shp_str, tif, categorical=True)
    check_scale_and_offset(tif)
    l_values,l_index = update_values_list_with_categorical(l_values, l_index, zonal_out, 'MCD12Q1.061', prefix='lc2_')
    return l_values, l_index

def attributes_from_glclu2019(geo_folder, dataset, shp_str, l_values, l_index):

    '''Calculates percentage occurrence of all classes in GLCLU2019 map'''

    tif = geo_folder / dataset / 'raw' / 'glclu2019_map.tif'
    zonal_out = zonal_stats(shp_str, tif, categorical=True)
    check_scale_and_offset(tif)
    l_values,l_index = update_values_list_with_categorical(l_values, l_index, zonal_out, 'GLCLU 2019', prefix='lc1_')
    return l_values, l_index


def attributes_from_era5(met_folder, shp_path, dataset, l_values, l_index, use_mfdataset=False):

    '''Calculates a variety of metrics from ERA5 data'''

    # Define various conversion constants
    water_density = 1000 # kg m-3
    mm_per_m = 1000 # mm m-1
    seconds_per_hour = 60*60 # s h-1
    seconds_per_day = seconds_per_hour*24 # s d-1
    days_per_month = np.array([31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]).reshape(-1, 1) # d month-1
    flip_sign = -1 # -; used to convert PET from negative (by convention this indicates an upward flux) to positive
    kelvin_to_celsius = -273.15
    pa_per_kpa = 1000 # Pa kPa-1

    # Define file locations, depending on if we are dealing with lumped or distributed cases
    if 'lumped' in shp_path:
        era_folder = met_folder / 'lumped'
    elif 'distributed' in shp_path:
        era_folder = met_folder / 'distributed'
    era_files = sorted( glob.glob( str(era_folder / 'ERA5_*.nc') ) )

    # Open the data
    if use_mfdataset:
        ds = xr.open_mfdataset(era_files, engine='netcdf4')
        ds = ds.load() # load the whole thing into memory instead of lazy-loading
    else:
        ds = xr.merge([xr.open_dataset(f) for f in era_files])
        ds = ds.load()
    
    # -- Get the precipitation for hydrologic signature calculations
    # This avoids having to reload the entire dataset another time
    ds_precip = ds['mtpr'].copy()    
    
    # Select whole years only
    #   This avoids issues in cases where we have incomplete whole data years
    #   (e.g. 2000-06-01 to 2007-12-31) in basins with very seasonal weather
    #   (e.g. all precip occurs in Jan, Feb, Mar). By using only full years
    #   we avoid accidentally biasing the attributes.
    ds = subset_dataset_to_max_full_years(ds)
    num_years = len(ds.groupby('time.year'))
    l_values.append(num_years)
    l_index.append( ('Climate', 'num_years_era5 ', 'years', 'ERA5') )
    
    # --- Annual statistics (aridity, seasonality, snow)
#    yearly_mper = ds['mper'].resample(time='1YE').mean() * flip_sign # Mean indicates to resample each year as that year's mean
#    yearly_mtpr = ds['mtpr'].resample(time='1YE').mean()
#    yearly_ari  = yearly_mper / yearly_mtpr
#    ari_m = yearly_ari.mean()
#    ari_s = yearly_ari.std()

    # --- Monthly attributes
    # Calculate monthly PET in mm
    #      kg m-2 s-1 / kg m-3
    # mm month-1 = kg m-2 s-1 * kg-1 m3 * s d-1 * d month-1 * mm m-1 * -
    monthly_mper = ds['mper'].resample(time='1ME').mean().groupby('time.month') 
    mper_m = monthly_mper.mean() / water_density * seconds_per_day * days_per_month * mm_per_m * flip_sign  # [kg m-2 s-1] to [mm month-1]; negative to indicate upward flux
    mper_s = monthly_mper.std() / water_density * seconds_per_day * days_per_month * mm_per_m  # [kg m-2 s-1] to [mm month-1]; negative to indicate upward flux
    l_values, l_index = process_monthly_means_to_lists(mper_m, 'mean', l_values, l_index, 'mper', 'mm')
    l_values, l_index = process_monthly_means_to_lists(mper_s, 'std', l_values, l_index, 'mper', 'mm')
        
    # Same for precipitation: [mm month-1]
    monthly_mtpr = ds['mtpr'].resample(time='1ME').mean().groupby('time.month')
    mtpr_m = monthly_mtpr.mean() / water_density * seconds_per_day * days_per_month * mm_per_m # [kg m-2 s-1] to [mm month-1]
    mtpr_s = monthly_mtpr.std() / water_density * seconds_per_day * days_per_month * mm_per_m # [kg m-2 s-1] to [mm month-1]
    l_values, l_index = process_monthly_means_to_lists(mtpr_m, 'mean', l_values, l_index, 'mtpr', 'mm')
    l_values, l_index = process_monthly_means_to_lists(mtpr_s, 'std', l_values, l_index, 'mtpr', 'mm')
    
    # Monthly temperature statistics [C]
    monthly_tavg = (ds['t'].resample(time='1D').mean().resample(time='1ME').mean() + kelvin_to_celsius).groupby('time.month')
    tavg_m = monthly_tavg.mean()
    tavg_s = monthly_tavg.std()
    l_values, l_index = process_monthly_means_to_lists(tavg_m, 'mean', l_values, l_index, 'tdavg', 'C')
    l_values, l_index = process_monthly_means_to_lists(tavg_s, 'std', l_values, l_index, 'tdavg', 'C')
    
    monthly_tmin = (ds['t'].resample(time='1D').min().resample(time='1ME').mean() + kelvin_to_celsius).groupby('time.month')
    tmin_m = monthly_tmin.mean()
    tmin_s = monthly_tmin.std()
    l_values, l_index = process_monthly_means_to_lists(tmin_m, 'mean', l_values, l_index, 'tdmin', 'C')
    l_values, l_index = process_monthly_means_to_lists(tmin_m, 'std', l_values, l_index, 'tdmin', 'C')
    
    monthly_tmax = (ds['t'].resample(time='1D').max().resample(time='1ME').mean() + kelvin_to_celsius).groupby('time.month')
    tmax_m = monthly_tmax.mean()
    tmax_s = monthly_tmax.std()
    l_values, l_index = process_monthly_means_to_lists(tmax_m, 'mean', l_values, l_index, 'tdmax', 'C')
    l_values, l_index = process_monthly_means_to_lists(tmax_s, 'std', l_values, l_index, 'tdmax', 'C')
    
    # Monthly shortwave and longwave [W m-2]
    monthly_sw = ds['msdwswrf'].resample(time='1ME').mean().groupby('time.month')
    sw_m = monthly_sw.mean()
    sw_s = monthly_sw.std()
    l_values, l_index = process_monthly_means_to_lists(sw_m, 'mean', l_values, l_index, 'msdwswrf', 'W m^-2')
    l_values, l_index = process_monthly_means_to_lists(sw_s, 'std', l_values, l_index, 'msdwswrf', 'W m^-2')
    
    monthly_lw = ds['msdwlwrf'].resample(time='1ME').mean().groupby('time.month')
    lw_m = monthly_lw.mean(dim='time')
    lw_s = monthly_lw.std(dim='time')
    l_values, l_index = process_monthly_means_to_lists(lw_m, 'mean', l_values, l_index, 'msdwlwrf', 'W m^-2')
    l_values, l_index = process_monthly_means_to_lists(lw_s, 'std', l_values, l_index, 'msdwlwrf', 'W m^-2')

    # Surface pressure [Pa]
    monthly_sp = ds['sp'].resample(time='1ME').mean().groupby('time.month')
    sp_m = monthly_sp.mean() / pa_per_kpa # [Pa] > [kPa]
    sp_s = monthly_sp.std() / pa_per_kpa
    l_values, l_index = process_monthly_means_to_lists(sp_m, 'mean', l_values, l_index, 'sp', 'kPa')
    l_values, l_index = process_monthly_means_to_lists(sp_s, 'std', l_values, l_index, 'sp', 'kPa')
    
    # Humidity [-]
    monthly_q = ds['q'].resample(time='1ME').mean().groupby('time.month') # specific
    q_m = monthly_q.mean()
    q_s = monthly_q.std()
    l_values, l_index = process_monthly_means_to_lists(q_m, 'mean', l_values, l_index, 'q', 'kg kg^-1')
    l_values, l_index = process_monthly_means_to_lists(q_s, 'std', l_values, l_index, 'q', 'kg kg^-1')
    
    monthly_rh = ds['rh'].resample(time='1ME').mean().groupby('time.month') # relative
    rh_m = monthly_rh.mean()
    rh_s = monthly_rh.std()
    l_values, l_index = process_monthly_means_to_lists(rh_m, 'mean', l_values, l_index, 'rh', 'kPa kPa^-1')
    l_values, l_index = process_monthly_means_to_lists(rh_s, 'std', l_values, l_index, 'rh', 'kPa kPa^-1')
    
    # Wind speed [m s-1]
    monthly_w = ds['w'].resample(time='1ME').mean().groupby('time.month')
    w_m = monthly_w.mean()
    w_s = monthly_w.std()
    l_values, l_index = process_monthly_means_to_lists(w_m, 'mean', l_values, l_index, 'w', 'm s^-1')
    l_values, l_index = process_monthly_means_to_lists(w_s, 'std', l_values, l_index, 'w', 'm s^-1')
    
    # Wind direction
    monthly_phi = ds['phi'].resample(time='1ME').apply(circmean_group).groupby('time.month')
    phi_m = monthly_phi.apply(circmean_group)
    phi_s = monthly_phi.apply(circstd_group)
    l_values, l_index = process_monthly_means_to_lists(phi_m, 'mean', l_values, l_index, 'phi', 'degrees')
    l_values, l_index = process_monthly_means_to_lists(phi_s, 'std', l_values, l_index, 'phi', 'degrees')
    
    # --- Long-term monthly statistics (aridity, seasonality, snow)
    # aridity
    monthly_mper = ds['mper'].resample(time='1ME').mean() * flip_sign
    monthly_mtpr = ds['mtpr'].resample(time='1ME').mean()
    if (monthly_mtpr == 0).any():
        print(f'--- WARNING: attributes_from_era5(): adding 1 mm to monthly precipitation to avoid divide by zero error in aridity calculation')
        monthly_mtpr[(monthly_mtpr == 0).sel(hru=0)] = 1 / mm_per_m * water_density / (seconds_per_day * days_per_month.mean()) # [mm month-1] / [mm m-1] * [kg m-3] / ([s d-1] * [d month-1]) = [kg m-2 s-1]
    monthly_ari = (monthly_mper / monthly_mtpr).groupby('time.month')
    ari_m = monthly_ari.mean()
    ari_s = monthly_ari.std()
    l_values, l_index = process_monthly_means_to_lists(ari_m, 'mean', l_values, l_index, 'aridity1', '-')
    l_values, l_index = process_monthly_means_to_lists(ari_s, 'std', l_values, l_index, 'aridity1', '-')

    # snow
    ds['snow'] = xr.where(ds['t'] < 273.15, ds['mtpr'],0)
    monthly_snow = ds['snow'].resample(time='1ME').mean()
    monthly_mtpr = ds['mtpr'].resample(time='1ME').mean()
    if (monthly_mtpr == 0).any():
        print(f'--- WARNING: attributes_from_era5(): adding 1 mm to monthly precipitation to avoid divide by zero error in snow calculation. Note that by definition this cannot change the fraction snow result (if there is 0 precip, none of it will fall as snow)')
        monthly_mtpr[(monthly_mtpr == 0).sel(hru=0)] = 1 / mm_per_m * water_density / (seconds_per_day * days_per_month.mean()) # [mm month-1] / [mm m-1] * [kg m-3] / ([s d-1] * [d month-1]) = [kg m-2 s-1]
    monthly_snow = (monthly_snow / monthly_mtpr).groupby('time.month')
    fsnow_m = monthly_snow.mean()
    fsnow_s = monthly_snow.std()
    l_values, l_index = process_monthly_means_to_lists(fsnow_m, 'mean', l_values, l_index, 'fracsnow1', '-')
    l_values, l_index = process_monthly_means_to_lists(fsnow_s, 'std', l_values, l_index, 'fracsnow1', '-')  
    
    # --- High-frequency statistics (high/low duration/timing/magnitude)
    #  Everyone does precip. We'll add temperature too as a drought/frost indicator
    #  ERA5 only
    
    # -- LOW TEMPERATURE
    variable  = 't'
    low_threshold = 273.15 # K, freezing point
    low_condition = ds[variable] < low_threshold
    l_values,l_index = calculate_temp_prcp_stats('temp',low_condition,'low',l_values,l_index)
    
    # -- HIGH TEMPERATURE
    # WMO defines a heat wave as a 5-day or longer period with maximum daily temperatures 5C above 
    # "standard" daily max temperature (1961-1990; source:
    # https://www.ifrc.org/sites/default/files/2021-06/10-HEAT-WAVE-HR.pdf).
    # We define a "hot day" therefore as a day with a maximum temperature 5 degrees over the 
    # the long-term mean maximum temperature.
    #   Note: we don't have 1961-1990 data for some stations, so we stick with long-term mean.
    #   Note: this will in most cases slightly underestimate heat waves compared to WMO definition
    
    # First, we identify the long-term mean daily maximum temperature in a dedicated function
    high_threshold = create_mean_daily_max_series(ds,var='t')
    
    # Next, we check if which 't' values are 5 degrees above the long-term mean daily max 
    #  ("(ds['t'] > result_array + 5)"), and resample this to a daily time series 
    #  ("resample(time='1D')") filled with "True" if any value in that day was True.
    daily_flags = (ds['t'] > high_threshold + 5).resample(time='1D').any()
    
    # Finally, we reindex these daily flags back onto the hourly time series by filling values
    high_condition = daily_flags.reindex_like(ds['t'], method='ffill')
    
    # Now calculate stats like before
    l_values,l_index = calculate_temp_prcp_stats('temp',high_condition,'high',l_values,l_index)
    
    # -- LOW PRECIPITATION
    variable = 'mtpr'
    # We'll stick with the original CAMELS definition of low precipitation: < 1 mm day-1
    # It may not make too much sense to look at "dry hours" so we'll do this analysis at daily step
    low_threshold = 1 # [mm d-1]
    # Create daily precipitation sum (divided by density, times mm m-1 cancels out)
    # [kg m-2 s-1] * [s h-1] / [kg m-3] * [mm m-1] = [mm h-1]
    low_condition = (ds[variable] * seconds_per_hour).resample(time='1D').sum() < low_threshold
    l_values,l_index = calculate_temp_prcp_stats('prec',low_condition,'low',l_values,l_index,
                                             units='days') # this 'units' argument prevents conversion to days inside the functiom
    
    # -- HIGH PRECIPITATION
    # CAMELS: > 5 times mean daily precip
    high_threshold = 5 * (ds[variable] * seconds_per_hour).resample(time='1D').sum().mean()
    high_condition = (ds[variable] * seconds_per_hour).resample(time='1D').sum() >= high_threshold
    l_values,l_index = calculate_temp_prcp_stats('prec',high_condition,'high',l_values,l_index,
                                                 units='days')

    return l_values, l_index, ds_precip, ds


def attributes_from_forest_height(geo_folder, dataset, shp_str, l_values, index):

    '''Calculates mean, min, max and stdv for forest height 2000 and 2020 tifs'''

    # Year 2000 min, mean, max, stdev
    tif = str( geo_folder / dataset / 'raw' / 'forest_height_2000.tif' )
    stats = ['mean', 'min', 'max', 'std']
    zonal_out = zonal_stats(shp_str, tif, stats=stats)
    scale,offset = read_scale_and_offset(tif)
    l_values = update_values_list(l_values, stats, zonal_out, scale, offset)
    index += [('Land cover', 'forest_height_2000_min',   'm', 'GLCLUC 2000-2020'),
              ('Land cover', 'forest_height_2000_mean',  'm', 'GLCLUC 2000-2020'),
              ('Land cover', 'forest_height_2000_max',   'm', 'GLCLUC 2000-2020'),
              ('Land cover', 'forest_height_2000_std',   'm', 'GLCLUC 2000-2020')]

    # Year 2020 mean, stdev
    tif = geo_folder / dataset / 'raw' / 'forest_height_2020.tif'
    stats = ['mean', 'min', 'max', 'std']
    zonal_out = zonal_stats(shp_str, tif, stats=stats)
    scale,offset = read_scale_and_offset(tif)
    l_values = update_values_list(l_values, stats, zonal_out, scale, offset)
    index += [('Land cover', 'forest_height_2020_min',   'm', 'GLCLUC 2000-2020'),
              ('Land cover', 'forest_height_2020_mean',  'm', 'GLCLUC 2000-2020'),
              ('Land cover', 'forest_height_2020_max',   'm', 'GLCLUC 2000-2020'),
              ('Land cover', 'forest_height_2020_std',   'm', 'GLCLUC 2000-2020')]

    return l_values, index

def attributes_from_lai(geo_folder, dataset, temp_path, shp_str, l_values, index):

    '''Calculates mean and stdv for tifs of monthly LAI values'''

    # Calculate monthly mean maps (e.g. mean Jan, Feb, etc.)
    lai_folder = geo_folder / dataset / 'raw' 
    lai_files = sorted( glob.glob(str(lai_folder / '*.tif')) ) # Find LAI files
    month_files = calculate_monthly_lai_maps(lai_files, temp_path) # Create 12 monthly maps

    # Monthly mean, stdev LAI; monthly mean, stdev GVF
    for month_file in month_files:
        stats = ['mean', 'std']
        zonal_out = zonal_stats(shp_str, month_file, stats=stats)
        scale, offset = read_scale_and_offset(month_file)
        scale,offset = read_scale_and_offset(month_file)
        l_values = update_values_list(l_values, stats, zonal_out, scale, offset)
        month = os.path.basename(month_file).split('_')[2]
        index += [('Land cover', f'lai_mean_month_{month}',  'm^2 m^-2', 'MCD15A2H.061'),
                  ('Land cover', f'lai_std_month_{month}',   'm^2 m^-2', 'MCD15A2H.061')]
    
    # Clear temp folder
    files_to_remove = os.listdir(temp_path)
    for file_to_remove in files_to_remove:
        file_remove_path = os.path.join(temp_path, file_to_remove)
        if os.path.isfile(file_remove_path):
            os.remove(file_remove_path)
    
    return l_values, index

def attributes_from_worldclim(geo_folder, dataset, shp_str, l_values, index):

    '''Calculates mean and stdv for tifs of monthly WorldClim values'''

    # Define file locations
    # Units source: https://www.worldclim.org/data/worldclim21.html
    clim_folder = geo_folder / dataset / 'raw'
    sub_folders =      ['prec', 'srad',   'tavg', 'tmax', 'tmin', 'vapr', 'wind',   'pet', 'aridity2', 'fracsnow2'] # aridity and fractionsnow have subscript 2 to distinguish them from ERA5 attributes
    sub_folder_units = ['mm',   'W m^-2', 'C',    'C',    'C',    'kPa',  'm s^-1', 'mm',  '-',       '-']  # srad original: kJ m^-2 d^-1. Converted below

    # Loop over the files and calculate the stats
    for sub_folder, sub_folder_unit in zip(sub_folders, sub_folder_units):
        month_files = sorted( glob.glob(str(clim_folder / sub_folder / '*.tif')) )
        for month_file in month_files:
            month_file = clim_folder / sub_folder / month_file # Construct the full path, because listdir() gives only files
            stats = ['mean', 'std']
            zonal_out = zonal_stats(shp_str, month_file, stats=stats)
            
            scale, offset = read_scale_and_offset(month_file)
            if sub_folder == 'srad':
                zonal_out = zonal_stats_unit_conversion(zonal_out,stats,'srad', scale, offset)

            l_values = update_values_list(l_values, stats, zonal_out, scale, offset)
            
            month = os.path.basename(month_file).split('_')[3].split('.')[0]
            var = os.path.basename(month_file).split('_')[2]
            source = 'WorldClim'
            if var == 'pet': source = 'WorldClim (derived, Oudin et al., 2005)'
            index += [('Climate', f'{var}_mean_month_{month}', f'{sub_folder_unit}',  source),
                      ('Climate', f'{var}_std_month_{month}', f'{sub_folder_unit}', source)]

    return l_values, index

## ------- Component functions
def get_geotif_nodata_value(tif):
    with rasterio.open(tif) as src:
        nodata_value = src.nodata
    return nodata_value

def check_scale_and_offset(tif):
    scale,offset = read_scale_and_offset(tif) # just to check we don't have any scale/offset going on
    if scale is None: scale = 1 # If scale is undefined that means we simply multiply by 1
    if offset is None: offset = 0 # Undefined offset > add 0
    if not (scale == 1) and not (offset == 0):
        print(f'--- WARNING: check_scale_and_offset(): scale or offset not 1 or 0 respectively.')
    return   

def get_categorical_dict(source):
    '''Contains dictionaries for categorical variables'''
    
    if source == 'GLCLU 2019':
        cat_dict = {1: 'true_desert',
                    2: 'semi_arid',
                    3: 'dense_short_vegetation',
                    4: 'open_tree_cover',
                    5: 'dense_tree_cover',
                    6: 'tree_cover_gain',
                    7: 'tree_cover_loss',
                    8: 'salt_pan',
                    9: 'wetland_sparse_vegetation',
                   10: 'wetland_dense_short_vegetation',
                   11: 'wetland_open_tree_cover',
                   12: 'wetland_dense_tree_cover',
                   13: 'wetland_tree_cover_gain',
                   14: 'wetland_tree_cover_loss',
                   15: 'ice',
                   16: 'water',
                   17: 'cropland',
                   18: 'built_up',
                   19: 'ocean',
                   20: 'no_data'}

    if source == 'MCD12Q1.061':
        cat_dict = {1: 'evergreen_needleleaf_forest',
                    2: 'evergreen_broadleaf_forest',
                    3: 'deciduous_needleleaf_forest',
                    4: 'deciduous_broadleaf_forest',
                    5: 'mixed_forest',
                    6: 'closed_shrubland',
                    7: 'open_shrubland',
                    8: 'woody_savanna',
                    9: 'savanna',
                   10: 'grassland',
                   11: 'permanent_wetland',
                   12: 'cropland',
                   13: 'urban_and_built_up',
                   14: 'cropland_natural_mosaic',
                   15: 'permanent_snow_ice',
                   16: 'barren',
                   17: 'water',
                  255: 'unclassified'}

    if source == 'LGRIP30':
        cat_dict = {0: 'water',
                    1: 'non_cropland',
                    2: 'irrigated_cropland',
                    3: 'rainfed_cropland'}
    
    return cat_dict

def update_values_list_with_categorical(l_values, l_index, zonal_out, source, prefix=''):
    '''Maps a zonal histogram of categorical classes onto descriptions and adds to lists'''

    # Get the category definitions
    cat_dict = get_categorical_dict(source)    

    # Find the total number of classified pixels
    total_pixels = 0
    for land_id,count in zonal_out[0].items():
        total_pixels += count
    
    # Loop over all categories and see what we have in this catchment
    for land_id,text in cat_dict.items():
        land_prct = 0
        if land_id in zonal_out[0].keys():
            land_prct = zonal_out[0][land_id] / total_pixels
        l_values.append(land_prct)
        l_index.append(('Land cover', f'{prefix}{text}_fraction', '-', f'{source}'))

    return l_values,l_index

def zonal_stats_unit_conversion(zonal_out, stat_to_convert, variable, scale, offset):
    '''Takes a zonal_stats output and converts the units of any variable listed in stat_to_convert'''

    # Constants
    j_per_kj = 1000 # [J kJ-1]
    seconds_per_day = 24*60*60 # [s day-1]

    # Keep track of scale and offset
    # Update scale and offset to usable values - we get None if scale and offset are 1 and 0 in the GeoTIFF
    if scale is None: scale = 1 # If scale is undefined that means we simply multiply by 1
    if offset is None: offset = 0 # Undefined offset > add 0

    #  We'll need code to handle this if these aren't 1 and 0 respectively
    if (scale != 1) or (offset !=0):
        print(f'--- ERROR: zonal_stats_unit_conversion(): code needed to deal with scale {scale} and offset {offset}')
        return -1

    # Select conversion factor
    if variable == 'srad':
        # From [kJ m-2 day-1] to [W m-2]:
        # [kJ m-2 day-1] * 1/[s day-1] * [J kJ-1] = [J m-2 s-1] = [W m-2]
        c_factor = 1/seconds_per_day * j_per_kj

    # loop over all list elements
    for list_id in range(0,len(zonal_out)):
        zonal_dict = zonal_out[list_id]

        # Loop over dictionary entries
        for key,val in zonal_dict.items():
            if key in stat_to_convert:
                zonal_out[list_id][key] = zonal_out[list_id][key] * c_factor

    return zonal_out

def update_values_list(l_values, stats, zonal_out, scale, offset):

    # Update scale and offset to usable values
    if scale is None: scale = 1 # If scale is undefined that means we simply multiply by 1
    if offset is None: offset = 0 # Undefined offset > add 0

    # We loop through the calculated stats in a pre-determined order:
    # 1. min
    # 2. mean
    # 3. max
    # 4. stdev
    # 5. ..
    if 'min' in stats:  l_values.append(zonal_out[0]['min']  * scale + offset)
    if 'mean' in stats: l_values.append(zonal_out[0]['mean'] * scale + offset)
    if 'max' in stats:  l_values.append(zonal_out[0]['max']  * scale + offset)
    if 'std' in stats:  l_values.append(zonal_out[0]['std']  * scale + offset)

    return l_values

def read_scale_and_offset(geotiff_path):
    # Open the GeoTIFF file
    dataset = gdal.Open(geotiff_path)

    if dataset is None:
        raise FileNotFoundError(f"File not found: {geotiff_path}")

    # Get the scale and offset values
    scale = dataset.GetRasterBand(1).GetScale()
    offset = dataset.GetRasterBand(1).GetOffset()

    # Close the dataset
    dataset = None

    return scale, offset

def get_geotif_data_as_array(file, band=1):
    ds = gdal.Open(file) # open the file
    band = ds.GetRasterBand(band) # get the data band
    data = band.ReadAsArray() # convert to numpy array for further manipulation   
    return data

def enforce_data_range(data,min,max,replace_with='limit'):

    '''Clamps data at min and max values'''

    if replace_with =='limit':
        data[data<min] = min
        data[data>max] = max
    else:
        data[data<min] = replace_with
        data[data>max] = replace_with
    
    return data

def write_geotif_sameDomain(src_file,des_file,des_data):
    
    # load the source file to get the appropriate attributes
    src_ds = gdal.Open(src_file)
    
    # get the geotransform
    des_transform = src_ds.GetGeoTransform()

    # Get the scale factor from the source metadata
    scale_factor = src_ds.GetRasterBand(1).GetScale()
    offset = src_ds.GetRasterBand(1).GetOffset()
    
    # get the data dimensions
    ncols = des_data.shape[1]
    nrows = des_data.shape[0]
    
    # make the file
    driver = gdal.GetDriverByName("GTiff")
    dst_ds = driver.Create(des_file,ncols,nrows,1,gdal.GDT_Float32, options = [ 'COMPRESS=DEFLATE' ])
    dst_ds.GetRasterBand(1).WriteArray( des_data )

    # Set the scale factor in the destination band
    if scale_factor: dst_ds.GetRasterBand(1).SetScale(scale_factor)
    if offset: dst_ds.GetRasterBand(1).SetOffset(offset)
    
    # Set the geotransform
    dst_ds.SetGeoTransform(des_transform)

    # Set the projection
    wkt = src_ds.GetProjection()
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    dst_ds.SetProjection( srs.ExportToWkt() )
    
    # close files
    src_ds = None
    des_ds = None

    return

def subset_dataset_to_max_full_years(ds, time='time', res='hour', water_year=False, debug=False) -> xr.Dataset:

    # Find final and end years
    start_year = ds[time][0].dt.year.values
    final_year = ds[time][-1].dt.year.values
    final_timestamp = ds[time][-1]

    # Set how a year is defined
    if water_year: # Oct-1 to Sep-30
        start_month = 10
        start_day   = 1
        final_month = 9
        final_day   = 30
    else: # Jan-1 to Dec-31
        start_month = 1
        start_day   = 1
        final_month = 12
        final_day   = 31

    # Find the first occurrence of Jan-01 00:00 as start of the subset
    for year in range(start_year,final_year):
        
        # Define the initial timestamp to check
        if res == 'hour':
            start_timestamp = pd.Timestamp(year,start_month,start_day,0,0,0)
        elif res == 'day':
            start_timestamp = pd.Timestamp(year,start_month,start_day)
        if debug: print(f'checking {start_timestamp}')

        # Select the subset of the dataset for the current duration
        # Note: if either start or final are not part of the time series,
        #  this will silently just use whatever is available
        subset_ds = ds.sel(time=slice(start_timestamp, final_timestamp))

        # Check if we actually selected the duration we requested
        subset_start = pd.Timestamp(subset_ds[time][0].values)
        if subset_start == start_timestamp:
            subset_start_year = year # Keep track of where we start
            break # stop searching. We've found the first occurrence of Jan-01

    # Find the last occurrence of Dec-31 23:00 as end of the subset
    for year in range(final_year,subset_start_year,-1):

        # Define the initial timestamp to check
        if res == 'hour':
            end_timestamp = pd.Timestamp(year,final_month,final_day,23,0,0)
        elif res == 'day':
            end_timestamp = pd.Timestamp(year,final_month,final_day)
        if debug: print(f'checking {end_timestamp}')

        # Select the subset of the dataset for the current duration
        subset_ds = ds.sel(time=slice(start_timestamp, end_timestamp))

        # Check if we actually selected the duration we requested
        subset_end = pd.Timestamp(subset_ds[time][-1].values)
        if subset_end == end_timestamp:
            subset_end_year = year # Keep track of where we start
            break # stop searching. We've found the first occurrence of Jan-01

    # Now check if we have selected a zero-year period
    # This would imply we have less than a full year of data
    # In this case, just return the original data set with a warning
    if len(subset_ds) == 0:
        print(f'--- WARNING: subset_dataset_to_max_full_years(): Found no full data years. Returning original DataArray')
        return ds
    else:   
        return subset_ds

# Define a function to map months to seasons
def get_season(month):
    if month in [12, 1, 2]:
        return 'djf'
    elif month in [3, 4, 5]:
        return 'mam'
    elif month in [6, 7, 8]:
        return 'jja'
    elif month in [9, 10, 11]:
        return 'son'

# Finds duration counts in a vector of True/False values
def find_durations(condition):
    '''Counts the duration(s) of True values in an xarray dataseries'''

    previous = False
    duration = 0
    durations = []
    for flag in condition.values:
        
        # Time step where we reset the count
        if not previous and flag:
            duration = 0 # New first timestep where condition = True, so duration = 0
            previous = True
    
        # Time step where we're in a sequence of condition = True
        if previous and flag:
            duration += 1 # Update duration, implicitly retain previous = True by not changing it
        
        # Time step where we reach the end of a condition = True duration
        if previous and not flag:
            durations.append(duration) # Save the maximum duration length to list
            previous = False # Update previous; duration will be reset next time we encounter a condition = True
    
        # Time step where we're in a continuation of condition = False
        if not previous and not flag:
            continue # do nothing
    
    return np.array(durations)

## ---- HYDROLOGY
def calculate_flow_period_stats(var, condition, hilo, l_values, l_index,
                                dataset='ERA5', units='hours', category='Climate'):
    
    '''Calculates frequency (mean), duration (mean, median, skew, kurtosis) and
        timing of periods identified with a certain condition'''
    
    # Constants. We want everything in [days] for consistency with original CAMELS
    hours_per_day = 24 # [hours day-1]
    days_per_year = 365.25 # [days year-1]
    
    # Calculate frequencies
    freq = condition.mean(dim='time') * days_per_year # [-] * [days year-1]
    l_values.append(float(freq.values))
    l_index.append( (f'{category}', f'{hilo}_{var}_freq', 'days year^-1', dataset) )
    
    # Calculate duration statistics
    durations = find_durations(condition) # [time steps]
    if len(durations) == 0:
        dur_mean = 0
        dur_med = 0
        dur_skew = 0
        dur_kur = 0
    else:
        dur_mean = np.mean(durations)
        dur_med = np.median(durations)
        dur_skew = skew(durations)
        dur_kur = kurtosis(durations)
    l_values.append(dur_mean) # [days]
    l_index.append((f'{category}', f'{hilo}_{var}_dur_mean', 'days', dataset) ) # Consistency with
    l_values.append(dur_med) # [days]
    l_index.append((f'{category}', f'{hilo}_{var}_dur_median', 'days', dataset) )
    l_values.append(dur_skew) # [-]
    l_index.append((f'{category}', f'{hilo}_{var}_dur_skew', '-', dataset) )
    l_values.append(dur_kur) # [-]
    l_index.append((f'{category}', f'{hilo}_{var}_dur_kurtosis', '-', dataset) )
    
    # Calculate timing statistic
    condition['season'] = ('time', 
        [get_season(month) for month in condition['time.month'].values]) # add seasons
    season_groups = condition.groupby('season')
    season_list   = list(season_groups.groups.keys())
    max_season_id = int(season_groups.sum().argmax(dim='season').values) # find season with most True values
    l_values.append(season_list[max_season_id]) # add season abbrev
    l_index.append( (f'{category}', f'{hilo}_{var}_timing', 'season', dataset) )
    
    return l_values, l_index

def calculate_signatures(hyd, pre, source, l_values, l_index):
    '''Calculates various signatures'''
    
    ## LONG-TERM STATISTICS
    # Mean daily discharge
    daily_mean_q = hyd['q_obs'].groupby(hyd['water_year']).mean() # .mean() of daily values gives us [mm d-1]
    mean_q_m = daily_mean_q.mean()
    mean_q_s = daily_mean_q.std()
    l_values.append(float(mean_q_m.values))
    l_index.append( ('Hydrology', 'daily_discharge_mean', 'mm d^-1', f'{source}') )
    l_values.append(float(mean_q_s.values))
    l_index.append( ('Hydrology', 'daily_discharge_std', 'mm d^-1', f'{source}') )
    
    # Mean monthly flows
    monthly_q = hyd['q_obs'].resample(time='1ME').mean().groupby('time.month')
    monthly_m = monthly_q.mean()
    monthly_s = monthly_q.std()
    l_values, l_index = process_monthly_means_to_lists(monthly_m, 'mean', l_values, l_index, 'daily_streamflow', 'mm day^-1', 'Hydrology', source)
    l_values, l_index = process_monthly_means_to_lists(monthly_s, 'std',  l_values, l_index, 'daily_streamflow', 'mm day^-1', 'Hydrology', source)
    
    # Runoff ratio
    daily_mean_p = pre.groupby(hyd['water_year']).mean()
    yearly_rr = daily_mean_q/daily_mean_p
    rr_m = yearly_rr.mean()
    rr_s = yearly_rr.std()
    l_values.append(float(rr_m.values))
    l_index.append( ('Hydrology', 'runoff_ratio_mean', 'mm d^-1 month-1', f'{source}, ERA5') )
    l_values.append(float(rr_s.values))
    l_index.append( ('Hydrology', 'runoff_ratio_std', 'mm d^-1 month-1', f'{source}, ERA5') )
    
    # Streamflow elasticity
    q_elas = np.median(((daily_mean_q - daily_mean_q.mean())/(daily_mean_p - daily_mean_p.mean()))*(daily_mean_p.mean()/daily_mean_q.mean()))
    l_values.append(q_elas)
    l_index.append( ('Hydrology', 'streamflow_elasticity', '-', f'{source}, ERA5') )
    
    # Slope of FDC
    groups = hyd['q_obs'].groupby(hyd['water_year'])
    slopes = []
    for year,group in groups:
        flows = group.values.copy()
        flows.sort()
        flows = np.log(flows)
        slope = (np.percentile(flows,66) - np.percentile(flows,33)) / (.66*len(flows) - .33*len(flows))
        slopes.append(slope)
    slopes = np.array(slopes)
    slope_m = slopes.mean()
    slope_s = slopes.std()
    
    l_values.append(slope_m)
    l_index.append( ('Hydrology', 'fdc_slope_mean', '-', f'{source}') )
    l_values.append(slope_s)
    l_index.append( ('Hydrology', 'fdc_slope_std', '-', f'{source}') )
    
    # Baseflow index
    rec,_ = baseflow.separation(hyd['q_obs'].values, method='Eckhardt') # Find baseflow with Eckhardt filter
    tmp = xr.DataArray(rec['Eckhardt'], dims='time', coords={'time': hyd['time']}) # Prepare a new variable to put in 'hyd'
    hyd['q_bas'] = tmp
    daily_mean_qbase = hyd['q_bas'].groupby(hyd['water_year']).mean()
    bfi_m = (daily_mean_qbase / daily_mean_q).mean()
    bfi_s = (daily_mean_qbase / daily_mean_q).std()
    l_values.append(float(bfi_m.values))
    l_index.append( ('Hydrology', 'bfi_mean', '-', f'{source}') )
    l_values.append(float(bfi_s.values))
    l_index.append( ('Hydrology', 'bfi_std', '-', f'{source}') )
    
    # Half-flow date
    tmp_cum_flow = hyd['q_obs'].groupby(hyd['water_year']).cumsum() # Cumulative flow per water year
    tmp_sum_flow = hyd['q_obs'].groupby(hyd['water_year']).sum() # Total flow per water year
    tmp_frc_flow = tmp_cum_flow.groupby(hyd['water_year']) / tmp_sum_flow # Fractional flow per water year
    groups = tmp_frc_flow.groupby(hyd['water_year'])
    dates = []
    for year,group in groups:
        hdf = group[group > 0.5][0]['time'].values
        dates.append(pd.Timestamp(hdf).dayofyear)
    dates = np.array(dates)
    hdf_m = circmean(dates, high=366)
    hdf_s = circstd(dates, high=366)
    l_values.append(hdf_m)
    l_index.append( ('Hydrology', 'hfd_mean', 'day of year', f'{source}') )
    l_values.append(hdf_s)
    l_index.append( ('Hydrology', 'hfd_std', 'day of year', f'{source}') )
    
    # Quantiles
    groups = hyd['q_obs'].groupby(hyd['water_year'])
    for quantile in [0.01, 0.05, 0.10, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]:
        q_m = groups.quantile(quantile).mean()
        q_s = groups.quantile(quantile).std()
        l_values.append(float(q_m.values))
        l_index.append(('Hydrology', f'q{int(quantile*100)}_mean', 'mm day^-1', f'{source}'))
        l_values.append(float(q_s.values))
        l_index.append(('Hydrology', f'q{int(quantile*100)}_std', 'mm day^-1', f'{source}'))
    
    # Durations
    variable  = 'q_obs'
    no_flow_threshold = 0 # mm d-1
    no_flow_condition = hyd[variable] < no_flow_threshold
    l_values,l_index  = calculate_flow_period_stats('flow',no_flow_condition,'no',l_values,l_index,dataset='source',units='days',category='Hydrology')
    
    low_flow_threshold = 0.2 * hyd['q_obs'].mean() # mm d-1
    low_flow_condition = hyd[variable] < low_flow_threshold
    l_values,l_index   = calculate_flow_period_stats('flow',low_flow_condition,'low',l_values,l_index,dataset='source',units='days',category='Hydrology')
    
    high_flow_threshold = 9 * hyd['q_obs'].median() # mm d-1
    high_flow_condition = hyd[variable] > high_flow_threshold
    l_values,l_index    = calculate_flow_period_stats('flow',high_flow_condition,'high',l_values,l_index,dataset='source',units='days',category='Hydrology')
    
    return l_values,l_index

## ---- HydroLAKES
def get_open_water_stats(gdf, att, mask, l_values, l_index):
    '''Calculates min, mean, max, std and total att ('Lake_area', 'Vol_total'), optionally using a reservoir mask'''

    # Setup
    if mask == 'all':
        mask = gdf.index >= 0 # Selects everything if no mask is provided
        type = 'open_water' # no reservoir mask, hence we're working with lakes
    elif mask == 'reservoir':
        mask = gdf['Lake_type'] == 2
        type = 'reservoir'
    if att == 'Lake_area':
        units = 'km^2' # https://data.hydrosheds.org/file/technical-documentation/HydroLAKES_TechDoc_v10.pdf
        att_d = 'area'
    elif att == 'Vol_total':
        units = 'million~m^3' # https://data.hydrosheds.org/file/technical-documentation/HydroLAKES_TechDoc_v10.pdf
        att_d = 'volume'

    # Stats
    l_values.append(gdf[mask][att].min())
    l_index.append(('Open water', f'{type}_{att_d}_min',  f'{units}', 'HydroLAKES'))
    l_values.append(gdf[mask][att].mean())
    l_index.append(('Open water', f'{type}_{att_d}_mean',  f'{units}', 'HydroLAKES'))
    l_values.append(gdf[mask][att].max())
    l_index.append(('Open water', f'{type}_{att_d}_max',  f'{units}', 'HydroLAKES'))
    l_values.append(gdf[mask][att].std())
    l_index.append(('Open water', f'{type}_{att_d}_std',  f'{units}', 'HydroLAKES'))
    l_values.append(gdf[mask][att].sum()) # total
    l_index.append(('Open water', f'{type}_{att_d}_total',  f'{units}', 'HydroLAKES'))
    
    return l_values, l_index

## ---- MERIT
def get_aspect_attributes(tif,l_values,l_index):
    '''Calculates circular statistics for MERIT Hydro aspect'''

    # Get data as a masked array - we know there are no-data values outside the catchment boundaries
    aspect = get_geotif_data_as_array(tif)
    no_data = get_geotif_nodata_value(tif)
    masked_aspect = np.ma.masked_array(aspect, aspect == no_data)

    ## Calculate the statistics
    l_values.append(masked_aspect.min())
    l_index.append(('Topography', 'aspect_min',  'degrees', 'MERIT Hydro'))
    
    l_values.append(circmean(masked_aspect,high=360))
    l_index.append(('Topography', 'aspect_mean',  'degrees', 'MERIT Hydro'))

    l_values.append(circstd(masked_aspect,high=360))
    l_index.append(('Topography', 'aspect_std',  'degrees', 'MERIT Hydro'))

    l_values.append(masked_aspect.max())
    l_index.append(('Topography', 'aspect_max',  'degrees', 'MERIT Hydro'))
    
    return l_values,l_index

def get_river_attributes(riv_str, l_values, l_index, area):
    
    '''Calculates topographic attributes from a MERIT Hydro Basins river polygon'''

    # Load shapefiles
    river = gpd.read_file(riv_str)
    river = river.set_index('COMID')
    
    # Raw data
    stream_lengths = []
    headwaters = river[river['maxup'] == 0] # identify reaches with no upstream
    for COMID in headwaters.index:
        stream_length = 0
        while COMID in river.index:
            stream_length += river.loc[COMID]['lengthkm'] # Add the length of the current segment
            COMID = river.loc[COMID]['NextDownID'] # Get the downstream reach
        stream_lengths.append(stream_length) # If we get here we ran out of downstream IDs
    
    # Stats
    stream_total = river['lengthkm'].sum()
    stream_lengths = np.array(stream_lengths)
    l_values.append(stream_lengths.min())
    l_index.append(('Topography', 'stream_length_min',  'km', 'MERIT Hydro Basins'))
    l_values.append(stream_lengths.mean())
    l_index.append(('Topography', 'stream_length_mean',  'km', 'MERIT Hydro Basins'))
    l_values.append(stream_lengths.max())
    l_index.append(('Topography', 'stream_length_max',  'km', 'MERIT Hydro Basins'))
    l_values.append(stream_lengths.std())
    l_index.append(('Topography', 'stream_length_std',  'km', 'MERIT Hydro Basins'))
    l_values.append(stream_total)
    l_index.append(('Topography', 'stream_length_total',  'km', 'MERIT Hydro Basins'))
    
    # Order
    l_values.append(river['order'].max())
    l_index.append(('Topography', 'steam_order_max',  '-', 'MERIT Hydro Basins'))

    # Derived
    density = stream_total/area
    elongation = 2*np.sqrt(area/np.pi)/stream_lengths.max()
    l_values.append(density)
    l_index.append(('Topography', 'stream_density',  'km^-1', 'Derived'))
    l_values.append(elongation)
    l_index.append(('Topography', 'elongation_ratio','-', 'Derived'))
    
    return l_values,l_index


## ---- ERA5
def calculate_temp_prcp_stats(var, condition, hilo, l_values,l_index,
                              dataset='ERA5', units='hours'):
    
    '''Calculates frequency (mean) and duration (mean, median, skew, kurtosis) 
        of temperature/precipitation periods'''

    # Constants. We want everything in [days] for consistency with original CAMELS
    hours_per_day = 24 # [hours day-1]
    days_per_year = 365.25 # [days year-1]

    # Calculate frequencies
    freq = condition.mean(dim='time') * days_per_year # [-] * [days year-1]
    l_values.append(freq.values[0])
    l_index.append( ('Climate', f'{hilo}_{var}_freq', 'days year^-1', dataset) )
    
    # Calculate duration statistics
    durations = find_durations(condition) # [time steps]
    if units == 'hours':
        durations = durations / hours_per_day # [days] = [hours] / [hours day-1]
    l_values.append(np.mean(durations)) # [days]
    l_index.append( ('Climate', f'{hilo}_{var}_dur_mean', 'days', dataset) ) # Consistency with
    l_values.append(np.median(durations)) # [days]
    l_index.append( ('Climate', f'{hilo}_{var}_dur_median', 'days', dataset) )
    l_values.append(skew(durations)) # [-]
    l_index.append( ('Climate', f'{hilo}_{var}_dur_skew', '-', dataset) )
    l_values.append(kurtosis(durations)) # [-]
    l_index.append( ('Climate', f'{hilo}_{var}_dur_kurtosis', '-', dataset) )

    # Calculate timing statistic
#    condition['season'] = ('time', 
#        [get_season(month) for month in condition['time.month'].values]) # add seasons
#    max_season_id = condition.groupby('season').sum().argmax(dim='season') # find season with most True values
#    l_values.append(condition.season[max_season_id].values[0]) # add season abbrev
#    l_index.append( ('Climate', f'{hilo}_{var}_timing', 'season', dataset) )
    condition['season'] = ('time', [get_season(month) for month in condition['time.month'].values]) # add seasons
    season_groups = condition.groupby('season')
    season_list   = list(season_groups.groups.keys())
    max_season_id = int(season_groups.sum().argmax(dim='season').values) # find season with most True values
    l_values.append(season_list[max_season_id]) # add season abbrev
    l_index.append( ('Climate', f'{hilo}_{var}_timing', 'season', dataset) )

    return l_values, l_index

# Finds a long-term mean daily maximum temperature in a way that doesn't rely on 
#  time.dt.dayofyear, because the latter will have Dec-31 as day 365 in non-leap-years,
#  and as 366 in leap years. Hence the day-of-year means do not use the same dates for 
#  a given DoY in leap years as they do in regular years.
def create_mean_daily_max_series(ds,var='t'):
    '''Finds the long-term mean daily maximum value of a variable'''
    
    # Create an array of all the month-days we have (e.g. 1949-12-31 00:00 becomes 1231)
    month_days_all = ds.time.dt.month * 100 + ds.time.dt.day

    # Loop over the unique month-days we have, and find the mean daily maximum value for each
    month_days_unique = np.unique(month_days_all)
    mean_daily_max = []
    for month_day in month_days_unique:
        val = ds[var].sel(time=(month_days_all==month_day)).groupby('time.year').max().mean().values
        mean_daily_max.append(val)

    # Convert the list to an array for further processing
    mean_daily_max = np.array(mean_daily_max)

    # Extract month_day values from the long xarray DataArray
    month_day_values = month_days_all.values
    
    # Find the indices of each month_day in the unique_month_days array
    indices = np.searchsorted(month_days_unique, month_day_values)
    
    # Use the indices to extract the corresponding data values
    corresponding_data_values = mean_daily_max[indices]
    
    # Create a new DataArray with the corresponding data values
    result_array = xr.DataArray(corresponding_data_values, 
                                coords=month_days_all.coords, 
                                dims=month_days_all.dims)

    return result_array


# Processing function to update the two main lists we're populating
def process_monthly_means_to_lists(da, stat, l_values, l_index, var, unit, category='Climate', source='ERA5'):
    '''Takes an xarray data array with monthly statistics and processes into l_values and l_index lists'''
    for month in range(1,13):
        val = da.sel(month=month).values.flatten()[0]
        txt = (f'{category}', f'{var}_{stat}_month_{month:02}', f'{unit}', f'{source}')
        l_values += [val] # Needs to be this way because we're appending to a list
        l_index  += [txt]
    return l_values, l_index

# Define custom functions to apply circmean and circstd to Xarray group objects
# Without this, xarray chokes on dimensions when converting the circmean/circstd output 
#  back into something with the right month indices
def circmean_group(group):
    return xr.DataArray(circmean(group, high=360, low=0), name='phi')
def circstd_group(group):
    return xr.DataArray(circstd(group, high=360, low=0), name='phi')

## ---- LAI
def filter_lai_files_by_date(files, last_n_years=[], last_n_months=[], last_n_days=[],
                                    years=[], months=[], days=[]):

    '''Filters list of LAI file names by last n years/months/days and/or by year/month/day x.
       Assumes date is given as 'yyyymmdd_*.tif', as part of the filename.
       Use years/months/days (input as list) to subset further.'''

    # Check inputs
    if (last_n_years and last_n_months) or \
       (last_n_years and last_n_days) or \
       (last_n_months and last_n_days):
        print('WARNING: filter_lai_files_by_date(): specify only one of last_n_years, last_n_months, last_n_days')
        return

    # Create a DatetimeIndex from filenames
    dates = []
    for file in files:
        file_name = os.path.basename(file)
        yyyymmdd = file_name[0:8]
        dates.append(yyyymmdd)
    dti = pd.to_datetime(dates,format='%Y%m%d')

    # Set the first entry
    start_date = dti[0]
    
    # Find the last entry
    last_year  = dti[-1].year
    last_month = dti[-1].month
    last_day   = dti[-1].day
    
    # Select the last n entries
    if last_n_years:    start_date = dti[-1] - relativedelta(years = last_n_years)
    elif last_n_months: start_date = dti[-1] - relativedelta(months = last_n_months)
    elif last_n_days:   start_date = dti[-1] - relativedelta(days = last_n_days)
    last_n = (dti >= start_date) & (dti <= dti[-1])

    # Specify filters to include all if no specific years/months/days were requested
    if not years:  years  = list(set(dti.year))  # i.e. filter to include all unique years in dti, \
    if not months: months = list(set(dti.month)) #    else use user input
    if not days:   days   = list(set(dti.day))
    mask = dti.year.isin(years) & dti.month.isin(months) & dti.day.isin(days)

    # Return the filtered list
    return [file for file, bool1, bool2 in zip(files,last_n,mask) if bool1 and bool2]

def calculate_monthly_lai_maps(lai_files, des_path):
    des_files = []
    for month in range(1,13):

        # Define valid data range
        # See docs, Table 4: https://lpdaac.usgs.gov/documents/926/MOD15_User_Guide_V61.pdf
        modis_min = 0
        modis_max = 100
    
        # Get the files we have for this month, for the last n years
        #print(f'Processing month {month:02d}')
        month_files = filter_lai_files_by_date(lai_files, months=[month])
    
        # Remove the one file we know is incomplete, 2022-10-16
        month_files = [file for file in month_files if '20221016' not in file]
        
        # Load the data as numpy arrays, stack vertically, and find the mean value (ignoring nan)
        data = [get_geotif_data_as_array(file) for file in month_files] # Get data as uint8
        stacked = np.dstack(data) # Create a 3D stack
        stacked_msk = np.ma.masked_array(stacked, mask=(stacked<modis_min) | (stacked>modis_max)) # Retain valid values only
        mean_lai = np.ma.mean(stacked_msk, axis=2)
    
        # Define the no-data locations
        #mean_all = np.nanmean(stacked, axis=2) # Any pixel that consistently has no-data in the source files (>= 249) should have a >= 249 mean
        #mean_lai[mean_all >= 249] = mean_all[mean_all >= 249] # Place the no-data values in the new monthly-mean-lai file
        
        # Define output file name and write to disk
        src_file = month_files[0] # We use this to copy over domain, projection, data scaling, etc
        des_file = str( des_path / f'month_mean_{month:02d}_MOD_Grid_MOD15A2H_Lai_500m.tif' )
        write_geotif_sameDomain(src_file, des_file, mean_lai)
        des_files.append(des_file)
    return des_files

## --- WorldClim
def aridity_and_fraction_snow_from_worldclim(geo_folder, dataset):
    
    '''Calculates aridity and fraction snow maps from WorldClim data'''

    # Find files
    clim_folder = geo_folder / dataset / 'raw'
    prc_files = sorted( glob.glob(str(clim_folder / 'prec' / '*.tif')) ) # [mm]
    pet_files = sorted( glob.glob(str(clim_folder / 'pet' / '*.tif')) ) # [mm]
    tmp_files = sorted( glob.glob(str(clim_folder / 'tavg' / '*.tif')) ) # [C]
    
    # Make the output locations
    ari_folder = clim_folder / 'aridity2'
    ari_folder.mkdir(parents=True, exist_ok=True)
    snow_folder = clim_folder / 'fracsnow2'
    snow_folder.mkdir(parents=True, exist_ok=True)

    # Loop over files and calculate aridity
    for prc_file, pet_file, tmp_file in zip(prc_files, pet_files, tmp_files):

        # Define month
        month = prc_file.split('_')[-1].split('.')[0] # 'wc2.1_30s_prec_01.tif' > '01', ..., '12'
        month_ix = int(month)-1 # -1 to account for zero-based indexing: Jan value is at index 0, not 1

        # Load data
        prc_path = clim_folder / 'prec' / prc_file
        pet_path = clim_folder / 'pet'  / pet_file
        tmp_path = clim_folder / 'tavg' / tmp_file      
        prc = get_geotif_data_as_array(prc_path) # [mm]
        pet = get_geotif_data_as_array(pet_path) # [mm]
        tmp = get_geotif_data_as_array(tmp_path) # [C]

        # Calculate variables
        snow = np.where(tmp < 0, prc, 0) # get snow first, because this needs precip and we'll (possibly) be updating the precip value below
        if (prc == 0).any():
            prc[prc == 0] = 1 # add 1 mm to avoid divide by zero errors
        ari = pet/prc # [-]
        frac_snow = snow/prc # [-]

        # Define output file name and write to disk
        ari_name = prc_file.replace('prec','aridity2')
        ari_file = str(ari_folder / ari_name)
        write_geotif_sameDomain(prc_path, ari_file, ari)
        snow_name = prc_file.replace('prec','fracsnow2')
        snow_file = str(snow_folder / snow_name)
        write_geotif_sameDomain(prc_path, snow_file, frac_snow)
    
    return

def oudin_pet_from_worldclim(geo_folder, dataset, debug=False):

    '''Calculates PET estimates from WorldClim data, using the Oudin (2005; 10.1016/j.jhydrol.2004.08.026) formulation'''

    # Constants
    lh = 2.45 # latent heat flux, MJ kg-1
    rw = 1000 # rho water, kg m-3
    days_per_month = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31] # days month-1
    mm_per_m = 1000 # mm m-1
    
    # Find files
    clim_folder = geo_folder / dataset / 'raw'
    srad_files = sorted( glob.glob(str(clim_folder / 'srad' / '*.tif')) ) # website says [kJ m-2 day-1], but paper says [MJ m-2 day-1]
    tavg_files = sorted( glob.glob(str(clim_folder / 'tavg' / '*.tif')) ) # C

    # Make the output location
    pet_folder = clim_folder / 'pet'
    pet_folder.mkdir(parents=True, exist_ok=True)

    # Loop over files and calculate PET
    for srad_file, tavg_file in zip(srad_files, tavg_files):

        # Define month
        month = srad_file.split('_')[-1].split('.')[0] # 'wc2.1_30s_srad_01.tif' > '01', ..., '12'
        month_ix = int(month)-1 # -1 to account for zero-based indexing: Jan value is at index 0, not 1
        
        # Load data
        srad_path = clim_folder / 'srad' / srad_file
        tavg_path = clim_folder / 'tavg' / tavg_file      
        srad = get_geotif_data_as_array(srad_path) / 1000 # [kJ m-2 day-1] / 1000 = [MJ m-2 day-1]
        tavg = get_geotif_data_as_array(tavg_path)
        
        # Oudin et al, 2005, Eq. 3
        pet = np.where(tavg+5 > 0, (srad / (lh*rw)) * ((tavg+5)/100) * mm_per_m, 0) # m day-1 > mm day-1
        pet_month = pet * days_per_month[month_ix] # mm month-1
        if debug: print(f'Calculating monthly PET for month {month} at day-index {month_ix}')

        # Define output file name and write to disk
        pet_name = srad_file.replace('srad','pet')
        pet_file = str(pet_folder / pet_name)
        write_geotif_sameDomain(srad_path, pet_file, pet_month)
    return