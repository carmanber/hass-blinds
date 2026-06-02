# -*- coding: utf-8 -*-
import sys
import types
import unittest


appdaemon_module = types.ModuleType("appdaemon")
plugins_module = types.ModuleType("appdaemon.plugins")
hass_module = types.ModuleType("appdaemon.plugins.hass")
hassapi_module = types.ModuleType("appdaemon.plugins.hass.hassapi")
yaml_module = types.ModuleType("yaml")


class FakeHass:
    pass


hassapi_module.Hass = FakeHass
yaml_module.safe_load = lambda _stream: {}
sys.modules.setdefault("appdaemon", appdaemon_module)
sys.modules.setdefault("appdaemon.plugins", plugins_module)
sys.modules.setdefault("appdaemon.plugins.hass", hass_module)
sys.modules.setdefault("appdaemon.plugins.hass.hassapi", hassapi_module)
sys.modules.setdefault("yaml", yaml_module)

from blinds import Blinds


class FakeBlind:
    DOWN = 0

    def __init__(self, position, angle):
        self.position = position
        self.angle = angle
        self.master_lock_unset = False

    def GetDesiredPosition(self):
        return self.position

    def GetDesiredAngle(self):
        return self.angle

    def UnsetMasterLock(self):
        self.master_lock_unset = True


class TestBlinds(unittest.TestCase):
    def setUp(self):
        self.app = Blinds.__new__(Blinds)
        self.app.args = {
            "blind": "cover.salle_manger",
            "tilt_mode": "immediate"
        }
        self.app.blind = "cover.salle_manger"
        self.app.blind_tilt = "cover.salle_manger"
        self.app.b = FakeBlind(position=0, angle=0)
        self.app.knx_current_angle = 100
        self.service_calls = []
        self.scheduled_calls = []
        self.logs = []

        self.app._safe_call_service = self._safe_call_service
        self.app.run_in = self._run_in
        self.app.log = self._log

    def _safe_call_service(self, domain, service, **kwargs):
        self.service_calls.append((domain, service, kwargs))

    def _run_in(self, callback, delay, **kwargs):
        self.scheduled_calls.append((callback, delay, kwargs))

    def _log(self, message, **kwargs):
        self.logs.append((message, kwargs))

    def test_immediate_tilt_sends_position_and_tilt_without_delay(self):
        self.app._move_blind(current_pos=100, current_angle=100)

        self.assertEqual([
            (
                "cover",
                "set_cover_position",
                {
                    "entity_id": "cover.salle_manger",
                    "position": 0
                }
            ),
            (
                "cover",
                "set_cover_tilt_position",
                {
                    "entity_id": "cover.salle_manger",
                    "tilt_position": 0
                }
            )
        ], self.service_calls)
        self.assertEqual([], [
            call for call in self.scheduled_calls
            if call[0] == self.app.set_tilt
        ])
        self.assertTrue(self.app.b.master_lock_unset)


if __name__ == "__main__":
    unittest.main()
