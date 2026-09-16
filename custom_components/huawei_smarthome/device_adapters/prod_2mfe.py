"""User-contributed protocol for Huawei product 2MFE (多合一空调新风地暖温控器).

设备类型: 温控器 (Thermostat), 型号 P14C
核心服务:
   airKey.{on,target,mode,gear}       RW  空调开关/温度/模式/风速
   heaterSwitch.on                   RW  地暖开关
   heaterTemperature.target          RW  地暖目标温度
   freshAirSwitch.on                 RW  新风开关
   freshAirMode.mode                 RW  新风模式
   freshAirFan.gear                  RW  新风风速
   antifreezingMode.on               RW  防冻模式
   temperature.current               R   室内温度(设备原始值为 0.1℃)
   faultDetection.{status,code}      R   故障状态/故障码

本适配器暴露:
   1. climate       空调、地暖
   2. fan           新风
   3. select        新风风速
   4. switch        防冻模式
   5. sensor        室内温度、故障状态、网络诊断
   6. binary_sensor 故障告警

厂商 H5 按 deviceFormSetting.mode 显示空调/新风/地暖，并通过 capabilitySet
位掩码筛选可用模式。H5 将室温除以 10，空调与地暖目标温度限制为 16-30℃；
本适配器使用相同规则。产品形态、安装温度上下限、重启及 OTA 由厂商应用管理。
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from .api import EntitySpec
from .context import DeviceContext

_TEMP_MIN = 16.0
_TEMP_MAX = 30.0

_AC_FORMS = frozenset((2, 4, 5, 6))
_FRESH_AIR_FORMS = frozenset((0, 3, 4, 6))
_HEATER_FORMS = frozenset((1, 3, 5, 6))
_KNOWN_FORMS = _AC_FORMS | _FRESH_AIR_FORMS | _HEATER_FORMS

_AC_MODES = {
    0: "auto",
    1: "dry",
    2: "fan_only",
    3: "heat",
    4: "cool",
}
_AC_FAN_MODES = {
    0: "auto",
    1: "low",
    2: "medium",
    3: "high",
    4: "very_low",
    5: "medium_high",
    6: "strong",
    7: "very_high",
}
_FRESH_AIR_MODES = {
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
_FRESH_AIR_GEARS = {
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


def _service(
    profile: Mapping[str, Any], sid: str
) -> Mapping[str, Any] | None:
    for service in profile.get("services", ()):
        if isinstance(service, Mapping) and service.get("serviceId") == sid:
            return service
    return None


def _field(
    profile: Mapping[str, Any], sid: str, name: str
) -> Mapping[str, Any] | None:
    service = _service(profile, sid)
    if service is None:
        return None
    for field in service.get("characteristics", ()):
        if isinstance(field, Mapping) and field.get("characteristicName") == name:
            return field
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _flag_value(value: Any) -> bool | None:
    number = _integer(value)
    if number == 1:
        return True
    if number == 0:
        return False
    return None


def _enum_labels(field: Mapping[str, Any]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for option in field.get("enumList", ()) or ():
        if not isinstance(option, Mapping):
            continue
        value = _integer(option.get("enumVal"))
        if value is None:
            continue
        labels[value] = str(option.get("descCh") or option.get("enumVal"))
    return labels


def _enum_state(
    context: DeviceContext,
    sid: str,
    field_name: str,
) -> str | None:
    profile = context.profile
    if not isinstance(profile, Mapping):
        return None
    field = _field(profile, sid, field_name)
    value = _integer(context.value(sid, field_name))
    if field is None or value is None:
        return None
    return _enum_labels(field).get(value)


def _capability_mask(context: DeviceContext, field_name: str) -> int | None:
    raw = context.value("capabilitySet", field_name)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        mask = int(str(raw).strip(), 16) if isinstance(raw, str) else int(raw)
    except (TypeError, ValueError):
        return None
    # The H5 treats 0 and 0xFEFE as missing/invalid capability values.
    return None if mask in (0, 0xFEFE) or mask < 0 else mask


def _capability_values(
    context: DeviceContext,
    field_name: str,
    mapping: Mapping[int, str],
) -> dict[int, str]:
    mask = _capability_mask(context, field_name)
    if mask is None:
        return dict(mapping)
    return {value: label for value, label in mapping.items() if mask & (1 << value)}


def _form_enabled(
    context: DeviceContext, allowed_forms: frozenset[int]
) -> bool:
    form = _integer(context.value("deviceFormSetting", "mode"))
    # Unknown/sentinel values must not hide controls confirmed by the Profile.
    return form is None or form not in _KNOWN_FORMS or form in allowed_forms


def _room_temperature(context: DeviceContext) -> float | None:
    raw = _number(context.value("temperature", "current"))
    return None if raw is None else round(raw / 10.0, 1)


def _target_temperature(context: DeviceContext, sid: str) -> float | None:
    return _number(context.value(sid, "target"))


def _clamped_temperature(value: Any) -> int:
    number = _number(value)
    if number is None:
        raise ValueError(f"2MFE invalid target temperature: {value!r}")
    return int(round(max(_TEMP_MIN, min(_TEMP_MAX, number))))


def _flag_action(sid: str, value: int):
    async def action(context: DeviceContext, data: Mapping[str, Any]) -> None:
        del data
        await context.async_send_service(sid, {"on": value})

    return action


def _flag_switch(
    key: str,
    name: str,
    sid: str,
) -> EntitySpec:
    return EntitySpec(
        platform="switch",
        key=key,
        name=name,
        state=lambda context: {"is_on": _flag_value(context.value(sid, "on"))},
        actions={
            "turn_on": _flag_action(sid, 1),
            "turn_off": _flag_action(sid, 0),
        },
    )


def _air_conditioner(
    context: DeviceContext, *, supports_fan: bool
) -> EntitySpec | None:
    mode_mapping = _capability_values(context, "acModeSet", _AC_MODES)
    if not mode_mapping:
        return None
    fan_mapping = (
        _capability_values(context, "acfanSpeedSet", _AC_FAN_MODES)
        if supports_fan
        else {}
    )
    hvac_to_device = {label: value for value, label in mode_mapping.items()}
    fan_to_device = {label: value for value, label in fan_mapping.items()}

    def state(device: DeviceContext) -> Mapping[str, Any]:
        mode = _integer(device.value("airKey", "mode"))
        gear = _integer(device.value("airKey", "gear"))
        result = {
            "current_temperature": _room_temperature(device),
            "target_temperature": _target_temperature(device, "airKey"),
            "hvac_mode": mode_mapping.get(mode),
        }
        if supports_fan:
            result["fan_mode"] = fan_mapping.get(gear)
        return result

    async def set_temperature(
        device: DeviceContext, data: Mapping[str, Any]
    ) -> None:
        await device.async_send_service(
            "airKey", {"target": _clamped_temperature(data.get("temperature"))}
        )

    async def set_hvac_mode(
        device: DeviceContext, data: Mapping[str, Any]
    ) -> None:
        mode = hvac_to_device.get(str(data.get("hvac_mode")))
        if mode is None:
            raise ValueError(f"2MFE unsupported HVAC mode: {data.get('hvac_mode')!r}")
        await device.async_send_service("airKey", {"mode": mode})

    async def set_fan_mode(
        device: DeviceContext, data: Mapping[str, Any]
    ) -> None:
        gear = fan_to_device.get(str(data.get("fan_mode")))
        if gear is None:
            raise ValueError(f"2MFE unsupported AC fan mode: {data.get('fan_mode')!r}")
        await device.async_send_service("airKey", {"gear": gear})

    actions = {
        "turn_on": _flag_action("airKey", 1),
        "turn_off": _flag_action("airKey", 0),
        "set_temperature": set_temperature,
        "set_hvac_mode": set_hvac_mode,
    }
    metadata: dict[str, Any] = {
        "hvac_modes": list(mode_mapping.values()),
        "min_temp": _TEMP_MIN,
        "max_temp": _TEMP_MAX,
    }
    if fan_mapping:
        metadata["fan_modes"] = list(fan_mapping.values())
        actions["set_fan_mode"] = set_fan_mode

    return EntitySpec(
        platform="climate",
        key="air_conditioner",
        name="空调",
        state=state,
        metadata=metadata,
        actions=actions,
    )


def _floor_heating() -> EntitySpec:
    def state(context: DeviceContext) -> Mapping[str, Any]:
        is_on = _flag_value(context.value("heaterSwitch", "on"))
        return {
            "current_temperature": _room_temperature(context),
            "target_temperature": _target_temperature(
                context, "heaterTemperature"
            ),
            "hvac_mode": None if is_on is None else ("heat" if is_on else "off"),
        }

    async def set_temperature(
        context: DeviceContext, data: Mapping[str, Any]
    ) -> None:
        await context.async_send_service(
            "heaterTemperature",
            {"target": _clamped_temperature(data.get("temperature"))},
        )

    async def set_hvac_mode(
        context: DeviceContext, data: Mapping[str, Any]
    ) -> None:
        mode = data.get("hvac_mode")
        if mode not in ("off", "heat"):
            raise ValueError(f"2MFE unsupported heating mode: {mode!r}")
        await context.async_send_service(
            "heaterSwitch", {"on": 0 if mode == "off" else 1}
        )

    return EntitySpec(
        platform="climate",
        key="floor_heating",
        name="地暖",
        state=state,
        metadata={
            "hvac_modes": ["off", "heat"],
            "min_temp": _TEMP_MIN,
            "max_temp": _TEMP_MAX,
        },
        actions={
            "turn_on": _flag_action("heaterSwitch", 1),
            "turn_off": _flag_action("heaterSwitch", 0),
            "set_temperature": set_temperature,
            "set_hvac_mode": set_hvac_mode,
        },
    )


def _fresh_air(
    context: DeviceContext, *, supports_mode: bool
) -> EntitySpec:
    mode_mapping = (
        _capability_values(context, "freshairModeSet", _FRESH_AIR_MODES)
        if supports_mode
        else {}
    )
    mode_to_device = {label: value for value, label in mode_mapping.items()}

    def state(device: DeviceContext) -> Mapping[str, Any]:
        mode = _integer(device.value("freshAirMode", "mode"))
        result = {
            "is_on": _flag_value(device.value("freshAirSwitch", "on")),
        }
        if supports_mode:
            result["preset_mode"] = mode_mapping.get(mode)
        return result

    async def set_preset_mode(
        device: DeviceContext, data: Mapping[str, Any]
    ) -> None:
        mode = mode_to_device.get(str(data.get("preset_mode")))
        if mode is None:
            raise ValueError(f"2MFE unsupported fresh-air mode: {data.get('preset_mode')!r}")
        await device.async_send_service("freshAirMode", {"mode": mode})

    async def turn_on(device: DeviceContext, data: Mapping[str, Any]) -> None:
        preset = data.get("preset_mode")
        if preset is not None:
            await set_preset_mode(device, {"preset_mode": preset})
        await device.async_send_service("freshAirSwitch", {"on": 1})

    actions = {
        "turn_on": turn_on,
        "turn_off": _flag_action("freshAirSwitch", 0),
    }
    metadata: dict[str, Any] = {}
    if mode_mapping:
        metadata["preset_modes"] = list(mode_mapping.values())
        actions["set_preset_mode"] = set_preset_mode

    return EntitySpec(
        platform="fan",
        key="fresh_air",
        name="新风",
        state=state,
        metadata=metadata,
        actions=actions,
    )


def _fresh_air_speed(context: DeviceContext) -> EntitySpec | None:
    gear_mapping = _capability_values(
        context, "freshairfanSpeedSet", _FRESH_AIR_GEARS
    )
    if not gear_mapping:
        return None
    gear_to_device = {label: value for value, label in gear_mapping.items()}

    async def select_option(
        device: DeviceContext, data: Mapping[str, Any]
    ) -> None:
        gear = gear_to_device.get(str(data.get("option")))
        if gear is None:
            raise ValueError(f"2MFE unsupported fresh-air gear: {data.get('option')!r}")
        await device.async_send_service("freshAirFan", {"gear": gear})

    return EntitySpec(
        platform="select",
        key="fresh_air_speed",
        name="新风风速",
        state=lambda device: {
            "current_option": gear_mapping.get(
                _integer(device.value("freshAirFan", "gear"))
            )
        },
        metadata={"options": list(gear_mapping.values())},
        actions={"select_option": select_option},
    )


def _sensor(
    key: str,
    name: str,
    sid: str,
    field_name: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> EntitySpec:
    return EntitySpec(
        platform="sensor",
        key=key,
        name=name,
        state=lambda context: {
            "native_value": _number(context.value(sid, field_name))
        },
        metadata=metadata or {},
    )


class Product2MFEAdapter:
    """P14C all-in-one HVAC thermostat adapter."""

    prod_id = "2MFE"

    def entities(self, context: DeviceContext) -> tuple[EntitySpec, ...]:
        profile = context.profile
        if not isinstance(profile, Mapping):
            return ()

        entities: list[EntitySpec] = []

        if _form_enabled(context, _AC_FORMS) and context.has_service(
            "airKey"
        ) and all(
            _field(profile, "airKey", name) is not None
            for name in ("on", "target", "mode")
        ):
            spec = _air_conditioner(
                context,
                supports_fan=_field(profile, "airKey", "gear") is not None,
            )
            if spec is not None:
                entities.append(spec)

        if (
            _form_enabled(context, _HEATER_FORMS)
            and context.has_service("heaterSwitch")
            and context.has_service("heaterTemperature")
            and _field(profile, "heaterSwitch", "on") is not None
            and _field(profile, "heaterTemperature", "target") is not None
        ):
            entities.append(_floor_heating())

        if (
            _form_enabled(context, _FRESH_AIR_FORMS)
            and context.has_service("freshAirSwitch")
            and _field(profile, "freshAirSwitch", "on") is not None
        ):
            entities.append(
                _fresh_air(
                    context,
                    supports_mode=(
                        context.has_service("freshAirMode")
                        and _field(profile, "freshAirMode", "mode") is not None
                    ),
                )
            )

        if (
            _form_enabled(context, _FRESH_AIR_FORMS)
            and context.has_service("freshAirFan")
            and _field(profile, "freshAirFan", "gear") is not None
        ):
            spec = _fresh_air_speed(context)
            if spec is not None:
                entities.append(spec)

        if (
            _form_enabled(context, _HEATER_FORMS)
            and context.has_service("antifreezingMode")
            and _field(profile, "antifreezingMode", "on") is not None
        ):
            entities.append(
                _flag_switch(
                    "antifreezing_mode",
                    "防冻模式",
                    "antifreezingMode",
                )
            )

        if context.has_service("temperature") and _field(
            profile, "temperature", "current"
        ) is not None:
            entities.append(
                EntitySpec(
                    platform="sensor",
                    key="room_temperature",
                    name="室内温度",
                    state=lambda device: {
                        "native_value": _room_temperature(device)
                    },
                    metadata={
                        "unit": "℃",
                        "device_class": "temperature",
                        "state_class": "measurement",
                    },
                )
            )

        if context.has_service("faultDetection"):
            if _field(profile, "faultDetection", "code") is not None:
                entities.append(
                    EntitySpec(
                        platform="sensor",
                        key="fault_state",
                        name="故障状态",
                        state=lambda device: {
                            "native_value": _enum_state(
                                device, "faultDetection", "code"
                            )
                        },
                    )
                )
            if _field(profile, "faultDetection", "status") is not None:
                entities.append(
                    EntitySpec(
                        platform="binary_sensor",
                        key="fault_problem",
                        name="故障告警",
                        state=lambda device: {
                            "is_on": _flag_value(
                                device.value("faultDetection", "status")
                            )
                        },
                        metadata={"device_class": "problem"},
                    )
                )

        if context.has_service("netInfo"):
            if _field(profile, "netInfo", "RSSI") is not None:
                entities.append(
                    _sensor(
                        "wifi_rssi",
                        "信号强度",
                        "netInfo",
                        "RSSI",
                        metadata={
                            "unit": "dBm",
                            "device_class": "signal_strength",
                            "state_class": "measurement",
                        },
                    )
                )
            if _field(profile, "netInfo", "intensity") is not None:
                entities.append(
                    EntitySpec(
                        platform="sensor",
                        key="wifi_level",
                        name="信号等级",
                        state=lambda device: {
                            "native_value": _enum_state(
                                device, "netInfo", "intensity"
                            )
                        },
                    )
                )
            for field_name, key, name in (
                ("SSID", "wifi_ssid", "Wi-Fi 名称"),
                ("IP", "wifi_ip", "IP 地址"),
                ("BSSID", "wifi_bssid", "BSSID"),
            ):
                if _field(profile, "netInfo", field_name) is None:
                    continue
                entities.append(
                    EntitySpec(
                        platform="sensor",
                        key=key,
                        name=name,
                        state=lambda device, field=field_name: {
                            "native_value": _text(device.value("netInfo", field))
                        },
                    )
                )

        return tuple(entities)


ADAPTER = Product2MFEAdapter()
