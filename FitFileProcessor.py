#!/usr/bin/env python

#
# copyright Tom Goetz
#

import logging, sys, datetime, traceback

import Fit
import GarminDB


logger = logging.getLogger(__file__)


class FitFileProcessor():

    def __init__(self, db_params_dict, debug):
        logger.info("Debug: %s", str(debug))
        self.db_params_dict = db_params_dict
        self.debug = debug

        self.garmin_db = GarminDB.GarminDB(db_params_dict, debug - 1)
        self.garmin_mon_db = GarminDB.MonitoringDB(self.db_params_dict, self.debug - 1)
        self.garmin_act_db = GarminDB.ActivitiesDB(self.db_params_dict, self.debug - 1)

    def write_generic(self, fit_file, message_type, messages):
        for message in messages:
            handler_name = 'write_' + message_type.name + '_entry'
            function = getattr(self, handler_name, None)
            if function is not None:
                function(fit_file, message)
            else:
                logger.debug("No entry handler %s for message type %s (%d) from %s: %s",
                    handler_name, repr(message_type), len(messages), fit_file.filename, str(messages[0]))

    def write_message_type(self, fit_file, message_type):
        messages = fit_file[message_type]
        function = getattr(self, 'write_' + message_type.name, self.write_generic)
        function(fit_file, message_type, messages)
        logger.debug("Processed %d %s entries for %s", len(messages), repr(message_type), fit_file.filename)

    def write_message_types(self, fit_file, message_types):
        logger.info("Importing %s (%s) [%s] with message types: %s", fit_file.filename, fit_file.time_created(), fit_file.type(), message_types)
        #
        # Some ordering is import: 1. create new file entries 2. create new device entries
        #
        priority_message_types = [Fit.MessageType.file_id, Fit.MessageType.device_info]
        for message_type in priority_message_types:
            self.write_message_type(fit_file, message_type)
        for message_type in message_types:
            if message_type not in priority_message_types:
                self.write_message_type(fit_file, message_type)

    def write_file(self, fit_file):
        self.lap = 1
        self.record = 1
        self.serial_number = None
        self.manufacturer = None
        self.product = None
        with self.garmin_db.managed_session() as self.garmin_db_session:
            with self.garmin_mon_db.managed_session() as self.garmin_mon_db_session:
                with self.garmin_act_db.managed_session() as self.garmin_act_db_session:
                    self.write_message_types(fit_file, fit_file.message_types())
                    # Now write a file's worth of data to the DB
                    self.garmin_act_db_session.commit()
                self.garmin_mon_db_session.commit()
            self.garmin_db_session.commit()


    #
    # Message type handlers
    #
    def write_file_id_entry(self, fit_file, message):
        parsed_message = message.to_dict()
        logger.info("file_id message: %s", repr(parsed_message))
        self.serial_number = parsed_message.get('serial_number', None)
        _manufacturer = GarminDB.Device.Manufacturer.convert(parsed_message.get('manufacturer', None))
        if _manufacturer is not None:
            self.manufacturer = _manufacturer
        self.product = parsed_message.get('product', None)
        if self.serial_number:
            device = {
                'serial_number' : self.serial_number,
                'timestamp'     : parsed_message['time_created'],
                'manufacturer'  : self.manufacturer,
                'product'       : Fit.FieldEnums.name_for_enum(self.product),
            }
            GarminDB.Device._find_or_create(self.garmin_db_session, device)
        (file_id, file_name) = GarminDB.File.name_and_id_from_path(fit_file.filename)
        file = {
            'id'            : file_id,
            'name'          : file_name,
            'type'          : GarminDB.File.FileType.convert(parsed_message['type']),
            'serial_number' : self.serial_number,
        }
        GarminDB.File._find_or_create(self.garmin_db_session, file)

    def write_stress_level_entry(self, fit_file, stress_message):
        parsed_message = stress_message.to_dict()
        stress = {
            'timestamp' : parsed_message['stress_level_time'],
            'stress'    : parsed_message['stress_level_value'],
        }
        GarminDB.Stress._find_or_create(self.garmin_db_session, stress)

    def write_event_entry(self, fit_file, event_message):
        logger.debug("event message: %s", repr(event_message.to_dict()))

    def write_software_entry(self, fit_file, software_message):
        logger.debug("software message: %s", repr(software_message.to_dict()))

    def write_file_creator_entry(self, fit_file, file_creator_message):
        logger.debug("file creator message: %s", repr(file_creator_message.to_dict()))

    def write_sport_entry(self, fit_file, sport_message):
        logger.debug("sport message: %s", repr(sport_message.to_dict()))

    def write_sensor_entry(self, fit_file, sensor_message):
        logger.debug("sensor message: %s", repr(sensor_message.to_dict()))

    def write_source_entry(self, fit_file, source_message):
        logger.debug("source message: %s", repr(source_message.to_dict()))

    def get_field_value(self, message_dict, field_name):
        return message_dict.get('dev_' + field_name, message_dict.get(field_name, None))

    def write_running_entry(self, fit_file, activity_id, sub_sport, message_dict):
        logger.debug("run entry: %s", repr(message_dict))
        run = {
            'activity_id'                       : activity_id,
            'steps'                             : self.get_field_value(message_dict, 'total_steps'),
            'avg_pace'                          : Fit.Conversions.speed_to_pace(message_dict.get('avg_speed', None)),
            'max_pace'                          : Fit.Conversions.speed_to_pace(message_dict.get('max_speed', None)),
            'avg_steps_per_min'                 : message_dict.get('avg_cadence', 0) * 2,
            'max_steps_per_min'                 : message_dict.get('max_cadence', 0) * 2,
            'avg_step_length'                   : self.get_field_value(message_dict, 'avg_step_length'),
            'avg_vertical_ratio'                : self.get_field_value(message_dict, 'avg_vertical_ratio'),
            'avg_vertical_oscillation'          : self.get_field_value(message_dict, 'avg_vertical_oscillation'),
            'avg_gct_balance'                   : self.get_field_value(message_dict, 'avg_stance_time_balance'),
            'avg_ground_contact_time'           : self.get_field_value(message_dict, 'avg_stance_time'),
            'avg_stance_time_percent'           : self.get_field_value(message_dict, 'avg_stance_time_percent'),
        }
        GarminDB.RunActivities._create_or_update_not_none(self.garmin_act_db_session, run)

    def write_walking_entry(self, fit_file, activity_id, sub_sport, message_dict):
        logger.debug("walk entry: %s", repr(message_dict))
        walk = {
            'activity_id'                       : activity_id,
            'steps'                             : self.get_field_value(message_dict, 'total_steps'),
            'avg_pace'                          : Fit.Conversions.speed_to_pace(message_dict.get('avg_speed', None)),
            'max_pace'                          : Fit.Conversions.speed_to_pace(message_dict.get('max_speed', None)),
        }
        GarminDB.WalkActivities._create_or_update_not_none(self.garmin_act_db_session, walk)

    def write_hiking_entry(self, fit_file, activity_id, sub_sport, message_dict):
        logger.debug("hike entry: %", repr(message_dict))
        return self.write_walking_entry(fit_file, activity_id, sub_sport, message_dict)

    def write_cycling_entry(self, fit_file, activity_id, sub_sport, message_dict):
        ride = {
            'activity_id'                        : activity_id,
            'strokes'                            : self.get_field_value(message_dict, 'total_strokes'),
        }
        logger.debug("ride entry: %s writing %s", repr(message_dict), repr(ride))
        GarminDB.CycleActivities._create_or_update_not_none(self.garmin_act_db_session, ride)

    def write_stand_up_paddleboarding_entry(self, fit_file, activity_id, sub_sport, message_dict):
        logger.debug("sup entry: %s", repr(message_dict))
        paddle = {
            'activity_id'                       : activity_id,
            'strokes'                           : self.get_field_value(message_dict, 'total_strokes'),
            'avg_stroke_distance'               : self.get_field_value(message_dict, 'avg_stroke_distance'),
        }
        GarminDB.PaddleActivities._create_or_update_not_none(self.garmin_act_db_session, paddle)

    def write_rowing_entry(self, fit_file, activity_id, sub_sport, message_dict):
        logger.debug("row entry: %s", repr(message_dict))
        return self.write_stand_up_paddleboarding_entry(fit_file, activity_id, sub_sport, message_dict)

    def write_elliptical_entry(self, fit_file, activity_id, sub_sport, message_dict):
        logger.debug("elliptical entry: %s", repr(message_dict))
        workout = {
            'activity_id'                       : activity_id,
            'steps'                             : message_dict.get('dev_Steps', message_dict.get('total_steps', None)),
            'elliptical_distance'               : message_dict.get('dev_User_distance', message_dict.get('dev_distance', message_dict.get('distance', None))),
        }
        GarminDB.EllipticalActivities._create_or_update_not_none(self.garmin_act_db_session, workout)

    def write_fitness_equipment_entry(self, fit_file, activity_id, sub_sport, message_dict):
        try:
            function = getattr(self, 'write_' + sub_sport.name + '_entry')
            function(fit_file, activity_id, sub_sport, message_dict)
        except AttributeError:
            logger.info("No sub sport handler type %s from %s: %s", sub_sport, fit_file.filename, str(message_dict))

    def write_alpine_skiing_entry(self, fit_file, activity_id, sub_sport, message_dict):
        logger.debug("Skiing entry: %s", repr(message_dict))

    def write_training_entry(self, fit_file, activity_id, sub_sport, message_dict):
        logger.debug("Training entry: %s", repr(message_dict))

    def write_session_entry(self, fit_file, message):
        logger.debug("session message: %s", repr(message.to_dict()))
        message_dict = message.to_dict()
        activity_id = GarminDB.File.id_from_path(fit_file.filename)
        sport = message_dict['sport']
        sub_sport = message_dict['sub_sport']
        activity = {
            'activity_id'                       : activity_id,
            'start_time'                        : message_dict['start_time'],
            'stop_time'                         : message_dict['timestamp'],
            'elapsed_time'                      : message_dict['total_elapsed_time'],
            'moving_time'                       : self.get_field_value(message_dict, 'total_timer_time'),
            'start_lat'                         : self.get_field_value(message_dict, 'start_position_lat'),
            'start_long'                        : self.get_field_value(message_dict, 'start_position_long'),
            'stop_lat'                          : self.get_field_value(message_dict, 'end_position_lat'),
            'stop_long'                         : self.get_field_value(message_dict, 'end_position_long'),
            'distance'                          : message_dict.get('dev_User_distance', message_dict.get('total_distance', None)),
            'cycles'                            : self.get_field_value(message_dict, 'total_cycles'),
            'laps'                              : self.get_field_value(message_dict, 'num_laps'),
            'avg_hr'                            : self.get_field_value(message_dict, 'avg_heart_rate'),
            'max_hr'                            : self.get_field_value(message_dict, 'max_heart_rate'),
            'calories'                          : self.get_field_value(message_dict, 'total_calories'),
            'avg_cadence'                       : self.get_field_value(message_dict, 'avg_cadence'),
            'max_cadence'                       : self.get_field_value(message_dict, 'max_cadence'),
            'avg_speed'                         : self.get_field_value(message_dict, 'avg_speed'),
            'max_speed'                         : self.get_field_value(message_dict, 'max_speed'),
            'ascent'                            : self.get_field_value(message_dict, 'total_ascent'),
            'descent'                           : self.get_field_value(message_dict, 'total_descent'),
            'max_temperature'                   : self.get_field_value(message_dict, 'max_temperature'),
            'avg_temperature'                   : self.get_field_value(message_dict, 'avg_temperature'),
            'training_effect'                   : self.get_field_value(message_dict, 'total_training_effect'),
            'anaerobic_training_effect'         : self.get_field_value(message_dict, 'total_anaerobic_training_effect')
        }
        # json metadata gives better values for sport and subsport, so use existing value if set
        current = GarminDB.Activities.get(self.garmin_act_db, activity_id)
        if current:
            if current.sport is None:
                activity['sport'] = sport.name
            if current.sub_sport is None:
                activity['sub_sport'] = sub_sport.name
        GarminDB.Activities._create_or_update_not_none(self.garmin_act_db_session, activity)
        try:
            function = getattr(self, 'write_' + sport.name + '_entry')
            function(fit_file, activity_id, sub_sport, message_dict)
        except AttributeError:
            logger.info("No sport handler for type %s from %s: %s", sport, fit_file.filename, str(message_dict))

    def write_device_settings_entry(self, fit_file, device_settings_message):
        logger.debug("device settings message: " + repr(device_settings_message.to_dict()))

    def write_lap_entry(self, fit_file, lap_message):
        message_dict = lap_message.to_dict()
        logger.debug("lap message: " + repr(message_dict))
        lap = {
            'activity_id'                       : GarminDB.File.id_from_path(fit_file.filename),
            'lap'                               : self.lap,
            'start_time'                        : self.get_field_value(message_dict, 'start_time'),
            'stop_time'                         : self.get_field_value(message_dict, 'timestamp'),
            'elapsed_time'                      : self.get_field_value(message_dict, 'total_elapsed_time'),
            'moving_time'                       : self.get_field_value(message_dict, 'total_timer_time'),
            'start_lat'                         : self.get_field_value(message_dict, 'start_position_lat'),
            'start_long'                        : self.get_field_value(message_dict, 'start_position_long'),
            'stop_lat'                          : self.get_field_value(message_dict, 'end_position_lat'),
            'stop_long'                         : self.get_field_value(message_dict, 'end_position_long'),
            'distance'                          : message_dict.get('dev_User_distance', message_dict.get('total_distance', None)),
            'cycles'                            : self.get_field_value(message_dict, 'total_cycles'),
            'avg_hr'                            : self.get_field_value(message_dict, 'avg_heart_rate'),
            'max_hr'                            : self.get_field_value(message_dict, 'max_heart_rate'),
            'calories'                          : self.get_field_value(message_dict, 'total_calories'),
            'avg_cadence'                       : self.get_field_value(message_dict, 'avg_cadence'),
            'max_cadence'                       : self.get_field_value(message_dict, 'max_cadence'),
            'avg_speed'                         : self.get_field_value(message_dict, 'avg_speed'),
            'max_speed'                         : self.get_field_value(message_dict, 'max_speed'),
            'ascent'                            : self.get_field_value(message_dict, 'total_ascent'),
            'descent'                           : self.get_field_value(message_dict, 'total_descent'),
            'max_temperature'                   : self.get_field_value(message_dict, 'max_temperature'),
            'avg_temperature'                   : self.get_field_value(message_dict, 'avg_temperature'),
        }
        GarminDB.ActivityLaps._create_or_update_not_none(self.garmin_act_db_session, lap)
        self.lap += 1

    def write_battery_entry(self, fit_file, battery_message):
        logger.debug("battery message: %s", repr(battery_message.to_dict()))

    def write_attribute(self, timestamp, parsed_message, attribute_name):
        attribute = parsed_message.get(attribute_name, None)
        if attribute is not None:
            GarminDB.Attributes._set_newer(self.garmin_db_session, attribute_name, attribute, timestamp)

    def write_user_profile_entry(self, fit_file, message):
        logger.debug("user profile message: %s", repr(message.to_dict()))
        parsed_message = message.to_dict()
        timestamp = fit_file.time_created()
        for attribute_name in [
                'Gender', 'height', 'Weight', 'Language', 'dist_setting', 'weight_setting', 'position_setting', 'elev_setting', 'sleep_time', 'wake_time'
            ]:
            self.write_attribute(timestamp, parsed_message, attribute_name)

    def write_activity_entry(self, fit_file, activity_message):
        logger.debug("activity message: %s", repr(activity_message.to_dict()))

    def write_zones_target_entry(self, fit_file, zones_target_message):
        logger.debug("zones target message: %s", repr(zones_target_message.to_dict()))

    def write_record_entry(self, fit_file, record_message):
        message_dict = record_message.to_dict()
        logger.debug("record message: %s", repr(message_dict))
        record = {
            'activity_id'                       : GarminDB.File.id_from_path(fit_file.filename),
            'record'                            : self.record,
            'timestamp'                         : self.get_field_value(message_dict, 'timestamp'),
            'position_lat'                      : self.get_field_value(message_dict, 'position_lat'),
            'position_long'                     : self.get_field_value(message_dict, 'position_long'),
            'distance'                          : self.get_field_value(message_dict, 'distance'),
            'cadence'                           : self.get_field_value(message_dict, 'cadence'),
            'hr'                                : self.get_field_value(message_dict, 'heart_rate'),
            'alititude'                         : self.get_field_value(message_dict, 'altitude'),
            'speed'                             : self.get_field_value(message_dict, 'speed'),
            'temperature'                       : self.get_field_value(message_dict, 'temperature'),
        }
        GarminDB.ActivityRecords._create_or_update_not_none(self.garmin_act_db_session, record)
        self.record += 1

    def write_dev_data_id_entry(self, fit_file, dev_data_id_message):
        logger.debug("dev_data_id message: %s", repr(dev_data_id_message.to_dict()))

    def write_field_description_entry(self, fit_file, field_description_message):
        logger.debug("field_description message: %s", repr(field_description_message.to_dict()))

    def write_monitoring_info_entry(self, fit_file, message):
        parsed_message = message.to_dict()
        activity_types = parsed_message['activity_type']
        if isinstance(activity_types, list):
            for index, activity_type in enumerate(activity_types):
                entry = {
                    'file_id'                   : GarminDB.File._get_id(self.garmin_db_session, fit_file.filename),
                    'timestamp'                 : parsed_message['local_timestamp'],
                    'activity_type'             : activity_type,
                    'resting_metabolic_rate'    : self.get_field_value(parsed_message, 'resting_metabolic_rate'),
                    'cycles_to_distance'        : parsed_message['cycles_to_distance'][index],
                    'cycles_to_calories'        : parsed_message['cycles_to_calories'][index]
                }
                GarminDB.MonitoringInfo._find_or_create(self.garmin_mon_db_session, entry)

    def write_monitoring_entry(self, fit_file, message):
        # Only include not None values so that we match and update only if a table's columns if it has values.
        entry = message.to_dict(ignore_none_values=True)
        try:
            intersection = GarminDB.MonitoringHeartRate.intersection(entry)
            if len(intersection) > 1 and intersection['heart_rate'] > 0:
                GarminDB.MonitoringHeartRate._create_or_update(self.garmin_mon_db_session, intersection)
            intersection = GarminDB.MonitoringIntensity.intersection(entry)
            if len(intersection) > 1:
                GarminDB.MonitoringIntensity._create_or_update(self.garmin_mon_db_session, intersection)
            intersection = GarminDB.MonitoringClimb.intersection(entry)
            if len(intersection) > 1:
                GarminDB.MonitoringClimb._create_or_update(self.garmin_mon_db_session, intersection)
            intersection = GarminDB.Monitoring.intersection(entry)
            if len(intersection) > 1:
                GarminDB.Monitoring._create_or_update(self.garmin_mon_db_session, intersection)
        except ValueError as e:
            logger.error("write_monitoring_entry: ValueError for %s: %s", repr(entry), traceback.format_exc())
        except Exception as e:
            logger.error("Exception on monitoring entry: %s: %s", repr(entry), traceback.format_exc())

    def write_device_info_entry(self, fit_file, device_info_message):
        try:
            parsed_message = device_info_message.to_dict()
            device_type = parsed_message.get('device_type', None)
            serial_number = parsed_message.get('serial_number', None)
            manufacturer = GarminDB.Device.Manufacturer.convert(parsed_message.get('manufacturer', None))
            product = parsed_message.get('product', None)
            source_type = parsed_message.get('source_type', None)
            # local devices are part of the main device. Base missing fields off of the main device.
            if source_type is Fit.FieldEnums.SourceType.local:
                if serial_number is None and self.serial_number is not None and device_type is not None:
                    serial_number = GarminDB.Device.local_device_serial_number(self.serial_number, device_type)
                if manufacturer is None and self.manufacturer is not None:
                    manufacturer = self.manufacturer
                if product is None and self.product is not None:
                    product = self.product
        except Exception as e:
            logger.warning("device_info: %s - %s", repr(parsed_message), str(e))
            raise ValueError('Failed to translate device_info')

        if serial_number is not None:
            device = {
                'serial_number'     : serial_number,
                'timestamp'         : parsed_message['timestamp'],
                'manufacturer'      : manufacturer,
                'product'           : Fit.FieldEnums.name_for_enum(product),
                'hardware_version'  : parsed_message.get('hardware_version', None),
            }
            try:
                GarminDB.Device._create_or_update_not_none(self.garmin_db_session, device)
            except Exception as e:
                logger.error("Device not written: %s - %s", repr(parsed_message), str(e))
            device_info = {
                'file_id'               : GarminDB.File._get_id(self.garmin_db_session, fit_file.filename),
                'serial_number'         : serial_number,
                'device_type'           : Fit.FieldEnums.name_for_enum(device_type),
                'timestamp'             : parsed_message['timestamp'],
                'cum_operating_time'    : parsed_message.get('cum_operating_time', None),
                'battery_voltage'       : parsed_message.get('battery_voltage', None),
                'software_version'      : parsed_message['software_version'],
            }
            try:
                GarminDB.DeviceInfo._create_or_update_not_none(self.garmin_db_session, device_info)
            except Exception as e:
                logger.warning("device_info not written: %s - %s", repr(parsed_message), str(e))
