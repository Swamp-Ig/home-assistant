"""Support for the iZone HVAC."""
import logging

from pizone import Zone, Controller, Listener

from homeassistant.components.climate import ClimateDevice
from homeassistant.components.climate.const import (
    STATE_AUTO, STATE_COOL, STATE_DRY, STATE_FAN_ONLY, STATE_HEAT,
    STATE_ECO, SUPPORT_FAN_MODE, SUPPORT_ON_OFF,
    SUPPORT_OPERATION_MODE, SUPPORT_TARGET_TEMPERATURE)
from homeassistant.const import (
    ATTR_TEMPERATURE, PRECISION_HALVES, TEMP_CELSIUS,
    STATE_OFF, STATE_ON)
from homeassistant.helpers.temperature import display_temp as show_temp
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from .const import DATA_ADD_ENTRIES, DATA_DISCOVERY_SERVICE, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistantType, config: ConfigType,
                            async_add_entities):
    """Initialize an IZone Controller."""
    disco = hass.data[DATA_DISCOVERY_SERVICE]

    # create any components not yet created
    for controller in disco.controllers.values():
        init_controller(controller, async_add_entities)

    # disco will use the register function to register any further components
    hass.data[DATA_ADD_ENTRIES] = async_add_entities

    return True


def init_controller(controller: Controller,
                    async_add_entries):
    """Register the controller device and the containing zones."""
    device = ControllerDevice(controller)
    async_add_entries([device])
    async_add_entries(device.zones.values())
    _LOGGER.info("Controller UID=%s added.", controller.device_uid)


class ControllerDevice(ClimateDevice):
    """Representation of iZone Controller."""

    def __init__(self, controller: Controller) -> None:
        """Initialise ControllerDevice."""
        self._controller = controller

        self._supported_features = (SUPPORT_OPERATION_MODE |
                                    SUPPORT_FAN_MODE | SUPPORT_ON_OFF)

        if ((controller.ras_mode == 'master' and controller.zone_ctrl == 13) or
                controller.ras_mode == 'RAS'):
            self._supported_features |= SUPPORT_TARGET_TEMPERATURE

        self._state_to_pizone = {
            STATE_COOL: Controller.Mode.COOL,
            STATE_HEAT: Controller.Mode.HEAT,
            STATE_AUTO: Controller.Mode.AUTO,
            STATE_FAN_ONLY: Controller.Mode.VENT,
            STATE_DRY: Controller.Mode.DRY,
        }
        if controller.free_air_enabled:
            self._state_to_pizone[STATE_ECO] = Controller.Mode.FREE_AIR

        self._fan_to_pizone = {}
        for fan in controller.fan_modes:
            self._fan_to_pizone[fan.name.title()] = fan
        self._available = True

        self._device_info = {
            'identifiers': {
                (DOMAIN, self.unique_id)
            },
            'name': self.name,
            'manufacturer': 'IZone',
            'model': self._controller.sys_type,
        }

        # Create the zones
        self.zones = {}
        for zone in controller.zones:
            self.zones[zone] = ZoneDevice(self, zone)

        # Register for connect/disconnect/update events
        device = self

        class ControllerListener(Listener):
            """Listener for controller related events."""

            def controller_disconnected(self, ctrl: Controller,
                                        ex: Exception) -> None:
                """Disconnected from contrller."""
                if ctrl is not controller:
                    return
                device.set_available(False, ex)

            def controller_reconnected(self, ctrl: Controller) -> None:
                """Reconnected to controller."""
                if ctrl is not controller:
                    return
                device.set_available(True)

            def controller_update(self, ctrl: Controller) -> None:
                """Handle controller data updates."""
                if ctrl is not controller:
                    return
                device.async_schedule_update_ha_state()
        listener = ControllerListener()
        controller.discovery.add_listener(listener)
        self.async_on_remove(
            lambda: controller.discovery.remove_listener(listener))

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    @property
    def assumed_state(self) -> bool:
        """Return True if unable to access real state of the entity."""
        return False

    def set_available(self, available: bool, ex: Exception = None) -> None:
        """
        Set availability for the controller.

        Also sets zone availability as they follow the same availability.
        """
        if self.available == available:
            return

        if available:
            _LOGGER.info(
                "Reconnected controller %s ",
                self._controller.device_uid)
        else:
            _LOGGER.info(
                "Controller %s disconnected due to exception: %s",
                self._controller.device_uid, ex)

        self._available = available
        self.async_schedule_update_ha_state()
        for zone in self.zones.values():
            zone.async_schedule_update_ha_state()

    @property
    def device_info(self):
        """Return the device info for the iZone system."""
        return self._device_info

    @property
    def unique_id(self):
        """Return the ID of the controller device."""
        return DOMAIN + '_' + self._controller.device_uid

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "iZone Controller " + self._controller.device_uid

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.

        False if entity pushes its state to HA.
        """
        return False

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._supported_features

    @property
    def temperature_unit(self):
        """Return the unit of measurement which this thermostat uses."""
        return TEMP_CELSIUS

    @property
    def precision(self):
        """Return the precision of the system."""
        return PRECISION_HALVES

    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        data = super().state_attributes
        data['supply_temperature'] = show_temp(
            self.hass, self.supply_temperature, self.temperature_unit,
            self.precision)
        data['temp_setpoint'] = show_temp(
            self.hass, self._controller.temp_setpoint,
            self.temperature_unit, self.precision)
        return data

    @property
    def current_operation(self):
        """Return current operation ie. heat, cool, idle."""
        if not self._controller.is_on:
            return STATE_OFF
        mode = self._controller.mode
        for (key, value) in self._state_to_pizone.items():
            if value == mode:
                return key
        assert False, "Should be unreachable"

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        return [STATE_OFF, *self._state_to_pizone]

    @property
    def current_temperature(self):
        """Return the current temperature."""
        if self._controller.mode == Controller.Mode.FREE_AIR:
            return self._controller.temp_supply
        return self._controller.temp_return

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if not self._supported_features & SUPPORT_TARGET_TEMPERATURE:
            return None
        return self._controller.temp_setpoint

    @property
    def supply_temperature(self):
        """Return the current supply, or in duct, temperature."""
        return self._controller.temp_supply

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return 0.5

    @property
    def is_on(self):
        """Return true if on."""
        return self._controller.is_on

    @property
    def current_fan_mode(self):
        """Return the fan setting."""
        return self._controller.fan.name.title()

    @property
    def fan_list(self):
        """Return the list of available fan modes."""
        return list(self._fan_to_pizone.keys())

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._controller.temp_min

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._controller.temp_max

    async def _wrap_and_catch(self, coro):
        try:
            await coro
        except ConnectionError as ex:
            self.set_available(False, ex)
        else:
            self.set_available(True)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature.

        This method must be run in the event loop and returns a coroutine.
        """
        if not self.supported_features & SUPPORT_TARGET_TEMPERATURE:
            self.async_schedule_update_ha_state(True)
            return
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self._wrap_and_catch(
                self._controller.set_temp_setpoint(temp))

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan mode.

        This method must be run in the event loop and returns a coroutine.
        """
        fan = self._fan_to_pizone[fan_mode]
        await self._wrap_and_catch(self._controller.set_fan(fan))

    async def async_set_operation_mode(self, operation_mode):
        """Set new target operation mode.

        This method must be run in the event loop and returns a coroutine.
        """
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
            return
        if not self._controller.is_on:
            await self.async_turn_on()
        if operation_mode != STATE_ON:
            mode = self._state_to_pizone[operation_mode]
            await self._wrap_and_catch(self._controller.set_mode(mode))

    async def async_turn_on(self):
        """Turn device on.

        This method must be run in the event loop and returns a coroutine.
        """
        await self._wrap_and_catch(self._controller.set_on(True))

    async def async_turn_off(self):
        """Turn device off.

        This method must be run in the event loop and returns a coroutine.
        """
        await self._wrap_and_catch(self._controller.set_on(False))


class ZoneDevice(ClimateDevice):
    """Representation of iZone Zone."""

    def __init__(self, controller: ControllerDevice, zone: Zone) -> None:
        """Initialise ZoneDevice."""
        self._controller = controller
        self._zone = zone
        self._name = zone.name.title()

        self._supported_features = (SUPPORT_ON_OFF |
                                    SUPPORT_OPERATION_MODE)
        if zone.type != Zone.Type.AUTO:
            self._state_to_pizone = {
                STATE_OFF: Zone.Mode.CLOSE,
                STATE_FAN_ONLY: Zone.Mode.OPEN,
            }
        else:
            self._state_to_pizone = {
                STATE_OFF: Zone.Mode.CLOSE,
                STATE_FAN_ONLY: Zone.Mode.OPEN,
                STATE_AUTO: Zone.Mode.AUTO,
            }
            self._supported_features |= SUPPORT_TARGET_TEMPERATURE

        self._device_info = {
            'identifiers': {
                (DOMAIN, controller.unique_id, zone.index)
            },
            'name': self.name,
            'manufacturer': 'IZone',
            'via_device': (DOMAIN, controller.unique_id),
            'model': zone.type.name.title(),
        }

        device = self

        # pylint: disable=protected-access
        class ZoneListener(Listener):
            """Listener for controller related events."""

            def zone_update(
                    self, ctrl: Controller,
                    zone: Zone) -> None:
                """Handle zone data updates."""
                if zone is not device._zone:
                    return
                device._name = zone.name.title()
                device.async_schedule_update_ha_state()

        listener = ZoneListener()
        controller._controller.discovery.add_listener(listener)
        self.async_on_remove(
            lambda: controller._controller.discovery.remove_listener(listener))

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._controller.available

    @property
    def assumed_state(self) -> bool:
        """Return True if unable to access real state of the entity."""
        return self._controller.assumed_state

    @property
    def device_info(self):
        """Return the device info for the iZone system."""
        return self._device_info

    @property
    def unique_id(self):
        """Return the ID of the controller device."""
        return "{}_z{}".format(
            self._controller.unique_id, self._zone.index+1)

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.

        False if entity pushes its state to HA.
        """
        return False

    @property
    def supported_features(self):
        """Return the list of supported features."""
        try:
            if self._zone.mode == Zone.Mode.AUTO:
                return self._supported_features
            return self._supported_features & ~SUPPORT_TARGET_TEMPERATURE
        except ConnectionError:
            return 0

    @property
    def temperature_unit(self):
        """Return the unit of measurement which this thermostat uses."""
        return TEMP_CELSIUS

    @property
    def precision(self):
        """Return the precision of the system."""
        return PRECISION_HALVES

    @property
    def current_operation(self):
        """Return current operation ie. heat, cool, idle."""
        mode = self._zone.mode
        for (key, value) in self._state_to_pizone.items():
            if value == mode:
                return key
        return ''

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        return list(self._state_to_pizone.keys())

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._zone.temp_current

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self._zone.type != Zone.Type.AUTO:
            return None
        return self._zone.temp_setpoint

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return 0.5

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._controller.min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._controller.max_temp

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature.

        This method must be run in the event loop and returns a coroutine.
        """
        if self._zone.mode != Zone.Mode.AUTO:
            return
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self._controller._wrap_and_catch(  # pylint: disable=W0212
                self._zone.set_temp_setpoint(temp))

    async def async_set_operation_mode(self, operation_mode):
        """Set new target operation mode.

        This method must be run in the event loop and returns a coroutine.
        """
        mode = self._state_to_pizone[operation_mode]
        await self._controller._wrap_and_catch(  # pylint: disable=W0212
            self._zone.set_mode(mode))
        self.async_schedule_update_ha_state()

    @property
    def is_on(self):
        """Return true if on."""
        return self._zone.mode != Zone.Mode.CLOSE

    async def async_turn_on(self):
        """Turn device on (open zone).

        This method must be run in the event loop and returns a coroutine.
        """
        if self._zone.type == Zone.Type.AUTO:
            await self._controller._wrap_and_catch(  # pylint: disable=W0212
                self._zone.set_mode(Zone.Mode.AUTO))
        else:
            await self._controller._wrap_and_catch(  # pylint: disable=W0212
                self._zone.set_mode(Zone.Mode.OPEN))
        self.async_schedule_update_ha_state()

    async def async_turn_off(self):
        """Turn device off (close zone).

        This method must be run in the event loop and returns a coroutine.
        """
        await self._controller._wrap_and_catch(  # pylint: disable=W0212
            self._zone.set_mode(Zone.Mode.CLOSE))
        self.async_schedule_update_ha_state()
