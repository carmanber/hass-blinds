# -*- coding: utf-8 -*-
import appdaemon.plugins.hass.hassapi as hass
from lib.sun_lib import Sun as SunLib
import datetime

class Sun(hass.Hass, SunLib):
  # Value taken from: https://www.extrica.com/article/21667/pdf IEEE A conversion guide: solar irradiance and lux illuminance
  RADIATION_LUX_CONV_RATE = 126 # Value taken to convert radiation to lux 

  def listen_sensor(self, key):
    if key in self.args:
        self.listen_state(self.lux, entity_id=self.args[key])

  def initialize(self):
    self.log("Initializing Sun data collector...")

    # Get brightness or radiation, brigntness is taken in priority over radiation to prevent conversion when possible 
    if "brightness_sensor" in self.args and "brightness_at_dawn_sensor" in self.args:
      self.listen_sensor("brightness_sensor")
      self.listen_sensor("brightness_at_dawn_sensor")
      self.sensor_conf = 'brightness_and_brightness_dawn'
      
    elif "brightness_sensor" in self.args and "radiation_at_dawn_sensor" in self.args:
      self.listen_sensor("brightness_sensor")
      self.listen_sensor("radiation_at_dawn_sensor")
      self.sensor_conf = 'brightness_and_radiation_dawn'

    elif "brightness_sensor" in self.args:
      self.listen_sensor("brightness_sensor")
      self.sensor_conf = 'brightness_only'

    elif "radiation_sensor" in self.args and "brightness_at_dawn_sensor" in self.args:
      self.listen_sensor("radiation_sensor")
      self.listen_sensor("brightness_at_dawn_sensor")
      self.sensor_conf = 'radiation_and_brightness_dawn'

    elif "radiation_sensor" in self.args and "radiation_at_dawn_sensor" in self.args:
      self.listen_sensor("radiation_sensor")
      self.listen_sensor("radiation_at_dawn_sensor")
      self.sensor_conf = 'radiation_and_radiation_dawn'

    elif "radiation_sensor" in self.args:
      self.listen_sensor("radiation_sensor")
      self.sensor_conf = 'radiation_only'

    else:
      self.log('No brightness or radiation_sensor provided', level="ERROR")

    self.log(f"Sun collector using {self.sensor_conf}")
    
    self.lux_values = []
    time = datetime.time(0, 0, 30)
    # Run every minute on the 30th second
    self.run_minutely(self.lux, time)


  def safe_float(self, val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


  def lux(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
    # self.log("lux values: %s" % self.lux_values)
    conf = self.sensor_conf
    s = self.args  # shortcut

    def gf(key):
        return self.safe_float(self.get_state(s.get(key)))

    # Retrieve value from sensor based on sun captor configuration
    # Convert radiation to lux when needed
    if conf == 'brightness_and_brightness_dawn':
      val_day, val_night = gf("brightness_sensor"), gf("brightness_at_dawn_sensor")

    elif conf == 'brightness_and_radiation_dawn':
      val_day, val_night = gf("brightness_sensor"), gf("radiation_at_dawn_sensor") * self.RADIATION_LUX_CONV_RATE

    elif conf == 'brightness_only':
      val_day = gf("brightness_sensor")
      val_night = val_day

    elif conf == 'radiation_and_brightness_dawn':
      val_day = gf("radiation_sensor") * self.RADIATION_LUX_CONV_RATE 
      val_night = gf("brightness_at_dawn_sensor")

    elif conf == 'radiation_and_radiation_dawn':
      val_day = gf("radiation_sensor") * self.RADIATION_LUX_CONV_RATE 
      val_night = gf("radiation_at_dawn_sensor") * self.RADIATION_LUX_CONV_RATE 

    elif self.sensor_conf == 'radiation_only':
      val_day = gf("radiation_sensor") * self.RADIATION_LUX_CONV_RATE 
      val_night = val_day
    
    else:
      self.log('No valid configuration detected for sun captor', level="ERROR")

    if val_day is None or val_night is None:
      self.log('Sensor values are None. Skipping.', level="WARNING")
      return

    _, average = self.get_lux(val_day, val_night)

    if average is not None:
      self.call_service("input_number/set_value", 
            entity_id="input_number.sun_lux_10_minute_average", value=average)
      self.log(f"Setting sun_lux_10_minute_average to {average:.2f}")

