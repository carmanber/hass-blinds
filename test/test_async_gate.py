import time
import appdaemon.plugins.hass.hassapi as hass

class TestAsyncGate(hass.Hass):

    def initialize(self):
        self.log("Initializing TestAsyncGate...")

        # Example call every 2 seconds (faster than the 5s rate-limit)
        self.run_every(self._test_gate, "now", 2)

        # Clean up log noise after 20s
        self.run_in(self._stop_test, 20)

    def _safe_call_service(self, domain, service, **kwargs):
        now = time.time()
        if hasattr(self, "last_cmd_ts") and now - self.last_cmd_ts < 5:
            self.log("[GATE] Skipping service call (rate-limit).", level="WARNING")
            return

        self.last_cmd_ts = now
        self.log(f"[GATE] Executing {domain}/{service} with {kwargs}", level="INFO")

        self.run_in(lambda _: self.call_service(domain + "/" + service, **kwargs), 0)

    def _test_gate(self, kwargs):
        """This is the simulated 'tick' repeatedly triggering a call."""
        self.log("Triggering test call ...")
        # Change this entity to one you can safely call, e.g., a light or cover
        self._safe_call_service(
            "cover", "set_cover_position",
            entity_id="cover.volet_salon_porte_fenetre", position=50
        )

    def _stop_test(self, kwargs):
        self.log("Stopping TestAsyncGate app.")
        self.cancel_timer(self._test_gate)