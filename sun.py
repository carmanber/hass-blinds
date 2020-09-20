# -*- coding: utf-8 -*-
import appdaemon.plugins.hass.hassapi as hass
from sun_lib import Sun
import datetime

class Sun(hass.Hass, Sun):

  def initialize(self):
    self.log("Initializing Sun data collector...")
    self.listen_state(self.lux, entity="sensor.wetter_zentrale_funktionen_helligkeit")
    self.listen_state(self.lux, entity="sensor.wetter_zentrale_funktionen_daemmerung")
    self.lux_values = []
    time = datetime.time(1, 1, 35)
    self.run_minutely(self.lux, time)

  def lux(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
    # self.log("lux values: %s" % self.lux_values)
    _, average = self.get_lux(
        self.get_state("sensor.wetter_zentrale_funktionen_helligkeit"),
        self.get_state("sensor.wetter_zentrale_funktionen_daemmerung"))

    if average is None:
      self.log('KNX lux values are unknown. Doing nothing.', level="ERROR")
      return

    self.call_service("input_number/set_value", 
        entity_id="input_number.sun_lux_10_minute_average", value=average) 
