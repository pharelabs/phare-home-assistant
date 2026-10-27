"""Numbers: the night path light's brightness, duration, ambient light threshold and movement
sensitivity, and the alarm settings' levels and timings."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import night_path, settings_catalog
from .coordinator import PhareConfigEntry, PhareCoordinator
from .entity import PhareSettingEntity


@dataclass(frozen=True, kw_only=True)
class PhareNumberDescription(NumberEntityDescription):
    setting: str
    # Displayed value = stored value / scale. The ambient light threshold is stored in hundredths
    # of a percent and shown in percent.
    scale: int = 1


NUMBERS: tuple[PhareNumberDescription, ...] = (
    PhareNumberDescription(
        key="night_path_brightness",
        translation_key="night_path_brightness",
        setting=night_path.BRIGHTNESS,
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    PhareNumberDescription(
        key="night_path_duration",
        translation_key="night_path_duration",
        setting=night_path.DURATION,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=1,
        native_max_value=600,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
    ),
    # The light turns on only below this much ambient light: 0.01 % is near-total darkness, 100 %
    # any room. The firmware compares in hundredths of a percent, so that is the step.
    PhareNumberDescription(
        key="night_path_light_threshold",
        translation_key="night_path_light_threshold",
        setting=night_path.LIGHT_THRESHOLD,
        scale=100,
        native_min_value=night_path.LIGHT_THRESHOLD_MIN / 100,
        native_max_value=night_path.LIGHT_THRESHOLD_MAX / 100,
        native_step=0.01,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
    ),
    # How long motion must last before the light turns on, in seconds: 0.1 s reacts to the
    # briefest movement (and to pets or curtains), 2 s needs someone walking. Stored in ms.
    PhareNumberDescription(
        key="night_path_sensitivity",
        translation_key="night_path_sensitivity",
        setting=night_path.CONFIRM_MS,
        scale=1000,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=night_path.SENSITIVITY_MIN_S,
        native_max_value=night_path.SENSITIVITY_MAX_S,
        native_step=night_path.SENSITIVITY_STEP_S,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.SLIDER,
    ),
)

ALARM_NUMBERS = tuple(s for s in settings_catalog.SETTINGS if s.kind == "number")


async def async_setup_entry(
    hass: HomeAssistant, entry: PhareConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()
    known_alarm: set[tuple[str, str]] = set()

    @callback
    def add_new() -> None:
        new: list[NumberEntity] = []
        for device in coordinator.data.devices.values():
            if night_path.has_light(device) and device.device_id not in known:
                known.add(device.device_id)
                new.extend(PhareNightPathNumber(coordinator, device.device_id, d) for d in NUMBERS)
            reported = coordinator.settings.get(device.device_id, {})
            has = settings_catalog.features(device.hw)
            for setting in ALARM_NUMBERS:
                if (device.device_id, setting.key) in known_alarm:
                    continue
                if setting.feature in has and setting.key in reported:
                    known_alarm.add((device.device_id, setting.key))
                    new.append(PhareAlarmSettingNumber(coordinator, device.device_id, setting))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PhareNightPathNumber(PhareSettingEntity, NumberEntity):
    entity_description: PhareNumberDescription
    _what = "night path light"

    def __init__(self, coordinator: PhareCoordinator, device_id: str, description: PhareNumberDescription) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value = self.setting(self.entity_description.setting)
        if value is None:
            return None
        return value / self.entity_description.scale if self.entity_description.scale != 1 else value

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        # The app's name for the sensitivity band the value falls in.
        if self.entity_description.setting != night_path.CONFIRM_MS:
            return None
        value = self.setting(night_path.CONFIRM_MS)
        return None if value is None else {"level": night_path.sensitivity_option(value)}

    async def async_set_native_value(self, value: float) -> None:
        stored = round(value * self.entity_description.scale)
        await self.write({self.entity_description.setting: stored})


class PhareAlarmSettingNumber(PhareSettingEntity, NumberEntity):
    """One alarm level or timing. A change that would leave a clear level above its level, or a
    ladder out of order, is refused before it is sent."""

    _what = "alarm setting"

    def __init__(self, coordinator: PhareCoordinator, device_id: str, setting: settings_catalog.Setting) -> None:
        super().__init__(coordinator, device_id, setting.key.lower())
        self._setting = setting
        self._attr_translation_key = setting.translation_key
        self._attr_native_min_value = setting.minimum
        self._attr_native_max_value = setting.maximum
        self._attr_native_step = setting.step
        self._attr_native_unit_of_measurement = setting.unit
        self._attr_device_class = NumberDeviceClass(setting.device_class) if setting.device_class else None
        self._attr_entity_registry_enabled_default = setting.enabled_default
        self._attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        return self.setting(self._setting.key)

    async def async_set_native_value(self, value: float) -> None:
        stored = round(value)
        current = {
            k: v for k in settings_catalog.LABELS
            if (v := self.setting(k)) is not None
        }
        if problem := settings_catalog.check(self._setting.key, stored, current):
            raise HomeAssistantError(problem)
        await self.write({self._setting.key: stored})
