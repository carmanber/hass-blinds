# -*- coding: utf-8
import time
import datetime
import math
import yaml
from lib.sun_lib import Sun
from lib.hysteresis_lib import Hysteresis

EVENING_HOUR_THRESHOLD = 15
DEFAULT_EVENT_KILL_SWITCH_DURATION = 30

def lower_function(x, lux, old_x):
    if lux < 8000:
        return True
    # Return false fast if the light is too intense.
    if lux > Sun.LUX_BLIND_DOWN_THRESHOLD:
        return False
    # This is the case when the sun is falling, no more linear formulars.
    if datetime.datetime.now().hour > EVENING_HOUR_THRESHOLD:
        return lux < Sun.LUX_BLIND_UP_THRESHOLD
    # only if the elevation is high enough, introduce a fixed threshold.
    if x > 30:
        return lux < Sun.LUX_BLIND_UP_THRESHOLD
    return lux < (3000 * x - 50000)


def upper_function(x, lux, old_x):
  if lux < 12000:
    return False
  if lux > Sun.LUX_BLIND_DOWN_THRESHOLD:
    return True
  # When the sun is falling, don't do linear comparisons anymore, this really only makese sense in the morning.
  if datetime.datetime.now().hour > EVENING_HOUR_THRESHOLD:
    return False
  return lux > (5000 * x - 55000)


class Blind:
  """Entscheidet über die Rolladen-Situation"""

  UP = 100
  DOWN = 0

  NO_CHANGE = False
  BLIND_NEEDS_MOVING = True

  KILL_SWITCH_ON = 1
  KILL_SWITCH_OFF_NO_CHANGE = 2
  KILL_SWITCH_OFF_CHANGE_NEEDED = 3

  def __init__(self, azimuth_entry, azimuth_exit, elevation=None, azimuth=None,
               wind_lock=False, lux_last_10_minutes=None,
               outside_temperature=None, inside_temperature=None,
               manual_night_control=False,
               manual_day_control=False, ledge=None,
               window_azimuth_position=None, window_type='window',
               window_open=False,
               # max inside temperatur is when the blinds go down on cold days (< 21 degree maximum)
               max_inside_temperature_cold_day=25.0,
               # min inside temperatur is when the blinds go down on warm days (> 21 degree maximum)
               max_inside_temperature_warm_day=21.0,
               lux_blind_up_threshold=Sun.LUX_BLIND_UP_THRESHOLD,
               lux_blind_down_threshold=Sun.LUX_BLIND_DOWN_THRESHOLD,
               lux_dark=Sun.LUX_DARK,
               kill_switch_hold_time=8,
               disable_tilt=False,
               window_height=240):
    # master lock prevents any further changes in blinds until released.
    self.master_lock = False
    self.elevation = elevation
    self.azimuth = azimuth
    self.azimuth_entry = azimuth_entry
    self.azimuth_exit = azimuth_exit
    self.lux_last_10_minutes = lux_last_10_minutes
    self.wind_lock = wind_lock
    self.ledge = ledge
    self.window_open = window_open
    # relative position of the window due to azimuth 0.
    self.window_azimuth_position = window_azimuth_position
    # self.kill_switch = kill_switch
    self.outside_temperature = outside_temperature
    self.inside_temperature = inside_temperature
    # when the blind should never go down in the nigh set this to true.
    self.manual_night_control = manual_night_control
    self.manual_day_control = manual_day_control
    self.window_type = window_type
    self.last_position = None
    self.last_angle = None
    self.desired_position = None
    self.desired_angle = None
    self.desired_position_reason = None
    self.kill_switch_timeout = None
    self.max_inside_temperature_cold_day = max_inside_temperature_cold_day
    self.max_inside_temperature_warm_day = max_inside_temperature_warm_day
    # a parameter that determines how long to hold the kill switch.
    # can be configured per blind. Unit is hours.
    self.kill_switch_hold_time = kill_switch_hold_time
    self.window_height = window_height
    self.blind_hysteresis = Hysteresis(None,
                                       None,
                                       lower_function,
                                       upper_function)
    self.temperature_hysteresis = Hysteresis(max_inside_temperature_cold_day - 0.5,
                                             max_inside_temperature_cold_day)
    self.previous_elevation = self.elevation
    self.logmsg = []
    self.disable_tilt = disable_tilt
    self.lux_dark = lux_dark

  def __str__(self):
    return yaml.dump(self.__dict__, default_flow_style=False)

  def log(self, msg):
    self.logmsg.append(msg)

  def SetDoorType(self):
    self.window_type='door'
    self.log('Blind treated as door')

  def SetWindowClosed(self):
    if self.window_type == 'door':
      self.SetKillSwitch(DEFAULT_EVENT_KILL_SWITCH_DURATION)

  def FlushLog(self):
    log = self.logmsg
    self.logmsg = []
    return log

  def SetAzimuth(self, value):
    self.azimuth = value

  def SetElevation(self, value):
    self.previous_elevation = self.elevation
    self.elevation = value

  def SetReedContact(self, value):
    self.window_open = value

  def SetOutsideTemperature(self, value):
    self.outside_temperature = value

  def SetInsideTemperature(self, value):
    self.inside_temperature = value

  def SetMaxOutsideTemperature(self, max_value, min_value):
    # Actually, we don't care about the maximum outside temperature (it is
    # calculated for the last day). What we do care about is if we should
    # assume it is generally warm outside or if it is generally cold outside.
    # We adapt the maximum inside temperature when we think it is a cold day,
    # same for a warm day. People have different feelings when blinds should go
    # down for cold and warm days. At least my wife has.
    # 24 outside - 21 inside
    # 22 outside - 22 inside
    # 20 outside - 23 inside
    # 18 outside - 24 inside
    # 16 outside - 25 inside

    avg = (self.max_inside_temperature_cold_day + self.max_inside_temperature_warm_day)/2.0
    if max_value > 24:
      self.temperature_hysteresis = Hysteresis(self.max_inside_temperature_warm_day - 0.5,
                                               self.max_inside_temperature_warm_day)
      return "Minimum temperature to get down blinds is: %sC (seems like warm season)" % self.max_inside_temperature_warm_day
    elif max_value < 16:
      self.temperature_hysteresis = Hysteresis(self.max_inside_temperature_cold_day - 0.5,
                                               self.max_inside_temperature_cold_day)
      return "Minimum temperature to get down blinds is: %sC (seems like cold season)" % self.max_inside_temperature_cold_day
    else:
      if min_value < 10:
        self.temperature_hysteresis = Hysteresis(self.max_inside_temperature_cold_day - 0.5,
                                                 self.max_inside_temperature_cold_day)
        return "Mornings are cold, we heat the building up to %sC." % self.max_inside_temperature_cold_day 
      offset = (max_value - 16) * 0.5
      threshold = 25 - offset
      self.temperature_hysteresis = Hysteresis(threshold - 0.5, threshold)
      return "Minimum temperature to get down blinds is: %sC (seems like transitional season)" % threshold

  def SetLux(self, average_value):
    self.lux_last_10_minutes = average_value


  def SetWindLock(self, wind_lock):
    self.wind_lock = wind_lock

  def SetKNXPositions(self, position, angle):
    self.knx_current_position = int(position)
    if angle is None:
      self.knx_current_angle = None
    else:
      self.knx_current_angle = int(angle)

  def SetDesiredPositions(self, position, angle, reason):
    self.desired_position = position
    if self.disable_tilt:
      self.desired_angle = None
    else:
      self.desired_angle = angle
    self.desired_position_reason = reason

  def SetLuxDark(self, threshold):
    self.lux_dark = threshold

  def SetMasterLock(self):
    self.master_lock = True

  def UnsetMasterLock(self):
    self.master_lock = False

  def GetMasterLock(self):
    return self.master_lock

  def GetDesiredPosition(self):
    return self.desired_position

  def GetDesiredAngle(self):
    if self.disable_tilt:
      return None
    return self.desired_angle

  def GetDesiredPositionReason(self):
    return self.desired_position_reason

  def UpdateLastPositionFromKNX(self):
    self.last_position = self.knx_current_position
    self.last_angle = self.knx_current_angle

  def UpdateLastStateFromDesiredState(self):
      self.last_position = self.desired_position
      self.last_angle = self.desired_angle

  def Evaluate(self):
    kill_switch_status = self.GetKillSwitch()

    if kill_switch_status == self.KILL_SWITCH_ON:
      return self._handle_kill_switch_on()

    reason = self.Control()
    self.desired_position_reason = reason
    self.log(f"[Evaluate] Raison: {reason}")

    if kill_switch_status == self.KILL_SWITCH_OFF_CHANGE_NEEDED:
      self._log_kill_switch_off()

    if self.last_position is None:
      self.UpdateLastPositionFromKNX()

    if self.desired_position is None and self.desired_angle is None:
      self.UpdateLastPositionFromKNX()
      return self.NO_CHANGE

    if self.desired_position is False and self.desired_angle is False:
      return self.NO_CHANGE

    if self._position_changed_by_user():
      return self.NO_CHANGE

    self.UpdateLastStateFromDesiredState()

    if self._positions_match():
      return self.NO_CHANGE

    return self.BLIND_NEEDS_MOVING

  def _handle_kill_switch_on(self):
    ts = datetime.datetime.fromtimestamp(self.kill_switch_timeout).strftime('%Y-%m-%d %H:%M:%S')
    reason = f"Kill Switch is on, release at {ts}"
    self.desired_position_reason = reason
    return self.NO_CHANGE

  def _log_kill_switch_off(self):
    self.log('Kill Switch was just turned off.')
    self.log(f"Desired Position: {self.desired_position}, Last Position: {self.last_position}, KNX Position: {self.knx_current_position}")

  def _position_changed_by_user(self):
    position_diff = abs(self.last_position - self.knx_current_position) > 10
    kill_switch_status = self.GetKillSwitch()

    if self.last_angle is not None and self.knx_current_angle is not None and not self.disable_tilt:
      angle_diff = abs(self.last_angle - self.knx_current_angle) > 10 
    else:
      angle_diff = False

    if not self.disable_tilt:
      if self.last_angle is None or self.knx_current_angle is None:
        return True

    if position_diff and kill_switch_status != self.KILL_SWITCH_OFF_CHANGE_NEEDED:
      self.log(f"Turning Kill switch on due to position mismatch: last_postion: {self.last_position}, knx_current_position: {self.knx_current_position}, last_angle: {self.last_angle}, knx_current_angle: {self.knx_current_angle}")
      self.SetKillSwitch(self.kill_switch_hold_time * 60)
      return True

    if angle_diff and kill_switch_status != self.KILL_SWITCH_OFF_CHANGE_NEEDED:
      self.log(f"Turning Kill switch on due to angle mismatch: last_postion: {self.last_position}, knx_current_position: {self.knx_current_position}, last_angle: {self.last_angle}, knx_current_angle: {self.knx_current_angle}")
      self.SetKillSwitch(DEFAULT_EVENT_KILL_SWITCH_DURATION)
      return True

    return False

  def _positions_match(self):
    return self.desired_position == self.knx_current_position and (
      self.desired_angle == self.knx_current_angle or self.disable_tilt)


  def SetKillSwitch(self, timeout):
    """ Timeout for kill switch in minutes. """
    now = int(time.time())
    # If already active (timeout in the future), do not refresh it.
    if getattr(self, 'kill_switch_timeout', None) and self.kill_switch_timeout > now:
      # Maintain reason but avoid extending the timer.
      self.desired_position_reason = "Kill Switch is ON"
      return
    # (Re)arm the kill switch
    try:
        timeout_minutes = int(timeout)
    except Exception:
        timeout_minutes = DEFAULT_EVENT_KILL_SWITCH_DURATION
    self.desired_position_reason = "Kill Switch is ON"
    self.kill_switch_timeout = now + timeout * 60


  def ReleaseKillSwitch(self):
    self.kill_switch_timeout = None # int(time.time())
    self.UpdateLastStateFromDesiredState()

  def GetKillSwitch(self):
    if self.kill_switch_timeout and self.kill_switch_timeout < int(time.time()):
      self.ReleaseKillSwitch()
      return self.KILL_SWITCH_OFF_CHANGE_NEEDED
    if self.kill_switch_timeout:
      return self.KILL_SWITCH_ON
    return self.KILL_SWITCH_OFF_NO_CHANGE

  def SunUnderLedge(self):
    # beschreibt wie hoch die Sonne in das Fenster eintritt (Wert ist vom
    # Dachvorsprung abwärts gemessen). Muss zwischen Abstand Dachvorsprung zur
    # oberen Fensterkante und Abstand Dachvorsprung Boden liegen. Ist der Wert
    # z.b. 3m bedeutet das, dass die Sonne quasi unterm Fenster, also auf der
    # Terasse auftrifft.

		# Die Berechnung unten hat 2 rechtwinklige Dreiecke zugrunde. 1. Dreieck
    # ist in Abhängigkeit Azimuth und wird auf den Dachvorsprung projiziert. 2.
		# Dreieck ist die errechnete Länge auf dem Dachvorsprung mit der Höhe des
		# Vorsprungs. Der Winkel ist die elevation.
    return (self.ledge * math.tan(math.radians(self.elevation)) / (
        math.cos(math.radians(self.window_azimuth_position - self.azimuth))) <
        self.window_height)

  def GetBlindSunAngle(self):
    # elevation = 0: angle = 0
    # elevation = 90: angle: 100
    # 90*x = 100
    # x = 100/90 = 1.11
    # /10 * 10 um 10er Schritte zu erreichen.
    #angle = int(round((1.111111 * self.elevation) / 10) * 10)
    # 0 fermé angle du soleil c est 0

    # Default to closed.
    angle = self.DOWN
    # Test formule on enleve 5° pour aller de 10° en 10° et etre au milieu
    angle = int(round((10.0/9.0 * self.elevation) / 10) * 10) 
    # if 0 < self.elevation < 23:
    #   angle = 50
    # elif 23 <= self.elevation < 33:
    #   angle = 70
    # elif 33 <= self.elevation < 43:
    #   angle = 80
    # elif self.elevation >= 43:
    #   angle = 100
    return angle

  def ManualDayControl(self):
    day = False
    if self.manual_day_control:
      if isinstance(self.manual_day_control, str):
        try:
          day = datetime.datetime.strptime(self.manual_day_control, "%H:%M").time() > datetime.datetime.now().time()
        except ValueError:
          self.log("The manual day control time is not in the correct format and will default to 12:00, please use the format HH:MM", level="WARNING")
          day = datetime.datetime.now().hour < 12
      elif isinstance(self.manual_day_control, bool):
        day = datetime.datetime.now().hour < 12
      else:
        self.log("The manual day control time is not in the correct format and will default to 12:00, please use the format HH:MM", level="WARNING")
        day = datetime.datetime.now().hour < 12
    return day


  def ManualNightControl(self):
    night = False
    if self.manual_night_control:
      if isinstance(self.manual_night_control, str):
        try:
          night = datetime.datetime.strptime(self.manual_night_control, "%H:%M").time() < datetime.datetime.now().time()
        except ValueError:
          self.log("The manual day control time is not in the correct format and will default to 22:00, please use the format HH:MM", level="WARNING")
          night = datetime.datetime.now().hour > 22
      elif isinstance(self.manual_night_control, bool):
        night = datetime.datetime.now().hour > 22
      else:
        self.log("The manual day control time is not in the correct format and will default to 22:00, please use the format HH:MM", level="WARNING")
        night = datetime.datetime.now().hour > 22
    return night


  def SunHitsWindow(self):
    if self.azimuth_entry < self.azimuth_exit:
      return (self.azimuth > self.azimuth_entry and
              self.azimuth < self.azimuth_exit)
    else:
      return (self.azimuth > self.azimuth_entry or
              self.azimuth < self.azimuth_exit)


  def DayLight(self):
    return self.lux_last_10_minutes > Sun.LUX_DAYLIGHT


  def IntenseSun(self):
    return self.blind_hysteresis.status_update(self.lux_last_10_minutes, self.elevation, self.previous_elevation)


  def Darkness(self):
    return self.lux_last_10_minutes < self.lux_dark


  def Dawn(self):
    return (self.lux_last_10_minutes >= Sun.LUX_DARK and
            self.lux_last_10_minutes <= Sun.LUX_DAYLIGHT)
  

  def DownBecauseOfDarkness(self):
    if self.manual_night_control:
      return self.DoNothing('it is dark, but this blind is controlled manually')
    # self.ReleaseKillSwitch()
    return self.Down(self.DOWN, self.DOWN, 'Down because of darkness')


  def DownBecauseOfSun(self):
    if self.ledge:
      if self.SunUnderLedge():
        return self.Down(self.DOWN, self.GetBlindSunAngle(),
               'Blind goes down, Sun is reaching under the ledge')
      else:
        return self.DoNothing('doing nothing, ledge protects us from sun')
    else:
      # no ledge.
      return self.Down(self.DOWN, self.GetBlindSunAngle(),
             'Sun hits the window, closing blind') # (%s lux, %s elevation, %s azimuth)' % (self.lux_last_10_minutes, self.elevation, self.azimuth))


  def DoNothing(self, reason):
    return self.SetDesiredPositions(None, None, reason)


  def Down(self, pos=DOWN, angle=DOWN, message='Blinds are down'):
    if self.window_open == True and self.window_type == 'door':
      return self.DoNothing('Blinds do not change on an open door')
    #if self.GetKillSwitch() == True:
    return self.SetDesiredPositions(pos, angle, message)


  def Up(self, message):
    #if self.GetKillSwitch():
      #return self.DoNothing('Raffstore Kill Switch is on')

    if self.ManualDayControl() or self.ManualNightControl():
      return self.DoNothing('Blinds are controlled manually')
    else:
      return self.SetDesiredPositions(self.UP, self.UP, message)


  def Control(self):
    if self.wind_lock:
      return self._handle_wind_lock()

    if self.Darkness():
      return self._handle_darkness()

    if self.ManualNightControl():
      return self._handle_manual_night()

    if self.Dawn():
      return self._handle_dawn()

    if not self.SunHitsWindow():
      return self._handle_no_direct_sun()

    return self._handle_direct_sun()

  def _handle_wind_lock(self):
    reason = 'Wind Alarm: blinds go up to prevent damage'
    self.SetDesiredPositions(self.UP, self.UP, reason)
    return reason

  def _handle_darkness(self):
    if self.lux_dark == Sun.LUX_DARK_WITH_LIGHT_INSIDE:
      self.lux_dark += 200
    reason = 'Down because of darkness'
    self.DownBecauseOfDarkness()
    return reason

  def _handle_manual_night(self):
    reason = 'Manual night control active'
    self.Down(self.DOWN, self.DOWN, reason)
    return reason

  def _handle_dawn(self):
    if datetime.datetime.now().hour > EVENING_HOUR_THRESHOLD:
      reason = 'It dawns in the evening, blinds go up'
      self.Up(reason)
    else:
      reason = 'Not enough sun, doing nothing'
      self.DoNothing(reason)
    return reason

  def _handle_no_direct_sun(self):
    if self.DayLight():
      extreme_heat = self._extreme_heat_test()
      if extreme_heat:
        reason = extreme_heat
        return reason

      reason = 'Sun has not reached the blind yet'
      self.Up(reason)
      return reason

    reason = 'Not enough daylight and sun does not hit window'
    self.DoNothing(reason)
    return reason

  def _handle_direct_sun(self):
    extreme_heat = self._extreme_heat_test()
    if extreme_heat:
      reason = extreme_heat
      return reason
    
    if not self.IntenseSun() and 5 <= self.azimuth < 40:
      if self.temperature_hysteresis.status_update(self.inside_temperature):
        reason = 'Low sun but hot morning, blinds go down'
        self.DownBecauseOfSun()
        return reason
      else:
        reason = 'Morning sun not strong enough'
        self.Up(reason)
        return reason
    else:
      if self.temperature_hysteresis.status_update(self.inside_temperature):
        reason = 'Intense sun + inside temp high -> blinds go down'
        self.DownBecauseOfSun()
        return reason
      else:
        reason = 'Intense sun but inside temp OK -> do nothing'
        self.DoNothing(reason)
        return reason

  def _extreme_heat_test(self):
    if (self.inside_temperature > 26.5 and
        self.outside_temperature > 30 and
        self.lux_last_10_minutes > 3000):
      reason = 'Extreme heat, all blinds close'
      self.Down(self.DOWN, self.DOWN, reason)
      return reason
    return False