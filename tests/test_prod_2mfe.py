from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ADAPTER_DIR = (
    Path(__file__).parents[1]
    / "custom_components"
    / "huawei_smarthome"
    / "device_adapters"
)
PACKAGE_NAME = "_adapter_2mfe_test"


def _load_adapter_module(
    package_name: str = PACKAGE_NAME, *, legacy_api: bool = False
):
    package = types.ModuleType(package_name)
    package.__path__ = [str(ADAPTER_DIR)]
    sys.modules[package_name] = package

    if legacy_api:
        api_module = types.ModuleType(f"{package_name}.api")

        class LegacyEntitySpec:
            def __init__(
                self,
                *,
                platform,
                key,
                name,
                state,
                metadata=None,
                actions=None,
                event_decoder=None,
            ) -> None:
                self.platform = platform
                self.key = key
                self.name = name
                self.state = state
                self.metadata = metadata or {}
                self.actions = actions or {}
                self.event_decoder = event_decoder

        api_module.EntitySpec = LegacyEntitySpec
        sys.modules[api_module.__name__] = api_module
    else:
        api_spec = importlib.util.spec_from_file_location(
            f"{package_name}.api", ADAPTER_DIR / "api.py"
        )
        assert api_spec is not None and api_spec.loader is not None
        api_module = importlib.util.module_from_spec(api_spec)
        sys.modules[api_spec.name] = api_module
        api_spec.loader.exec_module(api_module)

    context_module = types.ModuleType(f"{package_name}.context")
    context_module.DeviceContext = object
    sys.modules[context_module.__name__] = context_module

    adapter_spec = importlib.util.spec_from_file_location(
        f"{package_name}.prod_2mfe", ADAPTER_DIR / "prod_2mfe.py"
    )
    assert adapter_spec is not None and adapter_spec.loader is not None
    adapter_module = importlib.util.module_from_spec(adapter_spec)
    sys.modules[adapter_spec.name] = adapter_module
    adapter_spec.loader.exec_module(adapter_module)
    return adapter_module


def _characteristic(name: str, options: dict[int, str] | None = None):
    field = {"characteristicName": name}
    if options is not None:
        field["enumList"] = [
            {"enumVal": str(value), "descCh": label}
            for value, label in options.items()
        ]
    return field


class FakeContext:
    def __init__(self) -> None:
        ac_modes = {
            0: "自动",
            1: "除湿",
            2: "送风",
            3: "制热",
            4: "制冷",
            5: "自清洁",
        }
        ac_gears = {
            0: "自动",
            1: "低风",
            2: "中风",
            3: "高风",
            4: "超低风",
            5: "中高风",
            6: "强风",
            7: "超强风",
        }
        fresh_modes = {
            0: "自动",
            1: "内循环",
            2: "外循环",
            3: "混风",
            4: "夜间模式",
            5: "假日模式",
            6: "热交换",
            7: "定时模式",
            8: "手动模式",
            9: "内循环+除湿",
            10: "外循环+除湿",
            11: "混风+除湿",
            12: "防冻",
        }
        fresh_gears = {
            0: "自动",
            1: "超低风",
            2: "低风",
            3: "中风",
            4: "中高风",
            5: "高风",
            6: "强风",
            7: "超强风",
            8: "无风",
        }
        services = {
            "airKey": [
                _characteristic("on"),
                _characteristic("target"),
                _characteristic("mode", ac_modes),
                _characteristic("gear", ac_gears),
            ],
            "heaterSwitch": [_characteristic("on")],
            "heaterTemperature": [_characteristic("target")],
            "freshAirSwitch": [_characteristic("on")],
            "freshAirMode": [_characteristic("mode", fresh_modes)],
            "freshAirFan": [_characteristic("gear", fresh_gears)],
            "antifreezingMode": [_characteristic("on")],
            "temperature": [_characteristic("current")],
            "deviceFormSetting": [_characteristic("mode")],
            "capabilitySet": [
                _characteristic("acModeSet"),
                _characteristic("acfanSpeedSet"),
                _characteristic("freshairModeSet"),
                _characteristic("freshairfanSpeedSet"),
            ],
            "faultDetection": [
                _characteristic("status"),
                _characteristic(
                    "code", {0: "正常", 1: "设备异常", 101: "STA没有收到信标"}
                ),
            ],
            "netInfo": [
                _characteristic("RSSI"),
                _characteristic(
                    "intensity", {20: "0格信号", 40: "1格信号", 100: "4格信号"}
                ),
                _characteristic("SSID"),
                _characteristic("IP"),
                _characteristic("BSSID"),
            ],
        }
        self.profile = {
            "services": [
                {"serviceId": sid, "characteristics": fields}
                for sid, fields in services.items()
            ]
        }
        self.values = {
            "airKey": {"on": 1, "target": 24, "mode": 4, "gear": 3},
            "heaterSwitch": {"on": 1},
            "heaterTemperature": {"target": 22},
            "freshAirSwitch": {"on": 1},
            "freshAirMode": {"mode": 2},
            "freshAirFan": {"gear": 5},
            "antifreezingMode": {"on": 0},
            "temperature": {"current": 253},
            "deviceFormSetting": {"mode": 6},
            "capabilitySet": {
                "acModeSet": "001f",
                "acfanSpeedSet": "00ff",
                "freshairModeSet": "1fff",
                "freshairfanSpeedSet": "01ff",
            },
            "faultDetection": {"status": 1, "code": 101},
            "netInfo": {
                "RSSI": -62,
                "intensity": 100,
                "SSID": "Home",
                "IP": "192.0.2.10",
                "BSSID": "00:00:00:00:00:00",
            },
        }
        self.available = True
        self.commands: list[tuple[str, dict[str, object]]] = []

    def has_service(self, sid: str) -> bool:
        return any(
            service["serviceId"] == sid for service in self.profile["services"]
        )

    def value(self, sid: str, field: str):
        return self.values.get(sid, {}).get(field)

    async def async_send_service(self, sid: str, data) -> None:
        self.commands.append((sid, dict(data)))


class Product2MFEAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_adapter_module()

    def setUp(self) -> None:
        self.context = FakeContext()
        self.entities = {
            entity.key: entity
            for entity in self.module.ADAPTER.entities(self.context)
        }

    def test_exposes_all_in_one_thermostat_entities_and_states(self) -> None:
        self.assertEqual(self.module.ADAPTER.prod_id, "2MFE")
        self.assertEqual(
            set(self.entities),
            {
                "air_conditioner",
                "floor_heating",
                "fresh_air",
                "fresh_air_speed",
                "antifreezing_mode",
                "room_temperature",
                "fault_state",
                "fault_problem",
                "wifi_rssi",
                "wifi_level",
                "wifi_ssid",
                "wifi_ip",
                "wifi_bssid",
            },
        )
        self.assertEqual(
            self.entities["air_conditioner"].state(self.context),
            {
                "current_temperature": 25.3,
                "target_temperature": 24.0,
                "hvac_mode": "cool",
                "fan_mode": "high",
            },
        )
        self.assertEqual(
            self.entities["floor_heating"].state(self.context),
            {
                "current_temperature": 25.3,
                "target_temperature": 22.0,
                "hvac_mode": "heat",
            },
        )
        self.assertEqual(
            self.entities["fresh_air"].state(self.context),
            {"is_on": True, "preset_mode": "外循环"},
        )
        self.assertEqual(
            self.entities["fresh_air_speed"].state(self.context),
            {"current_option": "高风"},
        )
        self.assertEqual(
            self.entities["room_temperature"].state(self.context),
            {"native_value": 25.3},
        )
        self.assertEqual(
            self.entities["fault_state"].state(self.context),
            {"native_value": "STA没有收到信标"},
        )
        self.assertEqual(
            self.entities["fault_problem"].state(self.context), {"is_on": True}
        )
        self.assertEqual(
            self.entities["wifi_level"].state(self.context),
            {"native_value": "4格信号"},
        )

    def test_capability_masks_define_selectable_modes(self) -> None:
        self.assertEqual(
            self.entities["air_conditioner"].metadata["hvac_modes"],
            ["auto", "dry", "fan_only", "heat", "cool"],
        )
        self.assertEqual(
            self.entities["air_conditioner"].metadata["fan_modes"],
            [
                "auto",
                "low",
                "medium",
                "high",
                "very_low",
                "medium_high",
                "strong",
                "very_high",
            ],
        )
        self.assertEqual(
            self.entities["fresh_air"].metadata["preset_modes"],
            [
                "自动",
                "内循环",
                "外循环",
                "混风",
                "夜间模式",
                "假日模式",
                "热交换",
                "定时模式",
                "手动模式",
                "内循环+除湿",
                "外循环+除湿",
                "混风+除湿",
                "防冻",
            ],
        )

    def test_actions_use_h5_payloads_and_temperature_limits(self) -> None:
        actions = self.entities["air_conditioner"].actions
        asyncio.run(actions["turn_off"](self.context, {}))
        asyncio.run(
            actions["set_hvac_mode"](self.context, {"hvac_mode": "heat"})
        )
        asyncio.run(actions["set_fan_mode"](self.context, {"fan_mode": "high"}))
        asyncio.run(
            actions["set_temperature"](self.context, {"temperature": 40})
        )
        heater_actions = self.entities["floor_heating"].actions
        asyncio.run(
            heater_actions["set_hvac_mode"](self.context, {"hvac_mode": "off"})
        )
        asyncio.run(
            heater_actions["set_temperature"](
                self.context, {"temperature": 10}
            )
        )
        fresh_actions = self.entities["fresh_air"].actions
        asyncio.run(fresh_actions["turn_off"](self.context, {}))
        asyncio.run(
            fresh_actions["set_preset_mode"](
                self.context, {"preset_mode": "热交换"}
            )
        )
        asyncio.run(
            self.entities["fresh_air_speed"].actions["select_option"](
                self.context, {"option": "强风"}
            )
        )
        asyncio.run(
            self.entities["antifreezing_mode"].actions["turn_on"](
                self.context, {}
            )
        )
        self.assertEqual(
            self.context.commands,
            [
                ("airKey", {"on": 0}),
                ("airKey", {"mode": 3}),
                ("airKey", {"gear": 3}),
                ("airKey", {"target": 30}),
                ("heaterSwitch", {"on": 0}),
                ("heaterTemperature", {"target": 16}),
                ("freshAirSwitch", {"on": 0}),
                ("freshAirMode", {"mode": 6}),
                ("freshAirFan", {"gear": 6}),
                ("antifreezingMode", {"on": 1}),
            ],
        )

    def test_product_form_controls_exposed_entities(self) -> None:
        self.context.values["deviceFormSetting"]["mode"] = 0
        entities = {
            entity.key
            for entity in self.module.ADAPTER.entities(self.context)
        }

        self.assertNotIn("air_conditioner", entities)
        self.assertNotIn("floor_heating", entities)
        self.assertNotIn("antifreezing_mode", entities)
        self.assertIn("fresh_air", entities)
        self.assertIn("fresh_air_speed", entities)

    def test_unknown_product_form_keeps_control_entities(self) -> None:
        self.context.values["deviceFormSetting"]["mode"] = 255

        entities = {
            entity.key
            for entity in self.module.ADAPTER.entities(self.context)
        }

        self.assertIn("air_conditioner", entities)
        self.assertIn("floor_heating", entities)
        self.assertIn("fresh_air", entities)
        self.assertIn("fresh_air_speed", entities)
        self.assertIn("antifreezing_mode", entities)

    def test_constructs_entities_with_legacy_entity_spec_api(self) -> None:
        module = _load_adapter_module(
            "_adapter_2mfe_legacy_test", legacy_api=True
        )

        entities = module.ADAPTER.entities(FakeContext())

        self.assertIn("air_conditioner", {entity.key for entity in entities})

    def test_invalid_values_are_unknown_and_missing_profile_is_empty(self) -> None:
        self.context.values["temperature"]["current"] = "bad"
        self.context.values["airKey"]["mode"] = 99
        self.context.values["faultDetection"]["status"] = 3
        self.assertIsNone(
            self.entities["air_conditioner"].state(self.context)[
                "current_temperature"
            ]
        )
        self.assertIsNone(
            self.entities["air_conditioner"].state(self.context)["hvac_mode"]
        )
        self.assertEqual(
            self.entities["fault_problem"].state(self.context), {"is_on": None}
        )
        self.context.profile = None
        self.assertEqual(self.module.ADAPTER.entities(self.context), ())

    def test_optional_mode_and_fan_fields_are_not_advertised_when_missing(self) -> None:
        self.context.profile["services"] = [
            service
            for service in self.context.profile["services"]
            if service["serviceId"] != "freshAirMode"
        ]
        air_key = next(
            service
            for service in self.context.profile["services"]
            if service["serviceId"] == "airKey"
        )
        air_key["characteristics"] = [
            field
            for field in air_key["characteristics"]
            if field["characteristicName"] != "gear"
        ]

        entities = {
            entity.key: entity
            for entity in self.module.ADAPTER.entities(self.context)
        }
        self.assertNotIn("fan_modes", entities["air_conditioner"].metadata)
        self.assertNotIn("set_fan_mode", entities["air_conditioner"].actions)
        self.assertNotIn("preset_modes", entities["fresh_air"].metadata)
        self.assertNotIn("set_preset_mode", entities["fresh_air"].actions)


if __name__ == "__main__":
    unittest.main()
