# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import six

from st2common.util import reference
import st2common.util.action_db as action_utils
from st2common.constants.action import LIVEACTION_STATUS_CANCELED
from st2common.persistence.execution import ActionExecution
from st2common.persistence.action import RunnerType
from st2common.persistence.reactor import TriggerType, Trigger, TriggerInstance, Rule
from st2common.models.api.action import RunnerTypeAPI, ActionAPI, LiveActionAPI
from st2common.models.api.reactor import TriggerTypeAPI, TriggerAPI, TriggerInstanceAPI
from st2common.models.api.rule import RuleAPI
from st2common.models.db.execution import ActionExecutionDB
from st2common import log as logging


LOG = logging.getLogger(__name__)

SKIPPED = ['id', 'callback', 'action', 'runner_info']


def _decompose_liveaction(liveaction_db):
    """
    Splits the liveaction into an ActionExecution compatible dict.
    """
    decomposed = {'liveaction': {}}
    liveaction_api = vars(LiveActionAPI.from_model(liveaction_db))
    for k in liveaction_api.keys():
        if k in SKIPPED:
            decomposed['liveaction'][k] = liveaction_api[k]
        else:
            decomposed[k] = getattr(liveaction_db, k)
    return decomposed


def create_execution_object(liveaction, publish=True):
    action_db = action_utils.get_action_by_ref(liveaction.action)
    runner = RunnerType.get_by_name(action_db.runner_type['name'])

    attrs = {
        'action': vars(ActionAPI.from_model(action_db)),
        'runner': vars(RunnerTypeAPI.from_model(runner))
    }
    attrs.update(_decompose_liveaction(liveaction))

    if 'rule' in liveaction.context:
        rule = reference.get_model_from_ref(Rule, liveaction.context.get('rule', {}))
        attrs['rule'] = vars(RuleAPI.from_model(rule))

    if 'trigger_instance' in liveaction.context:
        trigger_instance_id = liveaction.context.get('trigger_instance', {})
        trigger_instance_id = trigger_instance_id.get('id', None)
        trigger_instance = TriggerInstance.get_by_id(trigger_instance_id)
        trigger = reference.get_model_by_resource_ref(db_api=Trigger,
                                                      ref=trigger_instance.trigger)
        trigger_type = reference.get_model_by_resource_ref(db_api=TriggerType,
                                                           ref=trigger.type)
        trigger_instance = reference.get_model_from_ref(
            TriggerInstance, liveaction.context.get('trigger_instance', {}))
        attrs['trigger_instance'] = vars(TriggerInstanceAPI.from_model(trigger_instance))
        attrs['trigger'] = vars(TriggerAPI.from_model(trigger))
        attrs['trigger_type'] = vars(TriggerTypeAPI.from_model(trigger_type))

    parent = ActionExecution.get(liveaction__id=liveaction.context.get('parent', ''))
    if parent:
        attrs['parent'] = str(parent.id)

    execution = ActionExecutionDB(**attrs)
    execution = ActionExecution.add_or_update(execution, publish=publish)

    if parent:
        if str(execution.id) not in parent.children:
            parent.children.append(str(execution.id))
            ActionExecution.add_or_update(parent)

    return execution


def update_execution(liveaction_db, publish=True):
    execution = ActionExecution.get(liveaction__id=str(liveaction_db.id))
    decomposed = _decompose_liveaction(liveaction_db)
    for k, v in six.iteritems(decomposed):
        setattr(execution, k, v)
    execution = ActionExecution.add_or_update(execution, publish=publish)
    return execution


def is_execution_canceled(execution_id):
    try:
        execution = ActionExecution.get_by_id(execution_id)
        return execution.status == LIVEACTION_STATUS_CANCELED
    except:
        return False  # XXX: What to do here?


class AscendingSortedDescendantView(object):
    def __init__(self):
        self._result = []

    def add(self, child):
        self._result.append(child)

    @property
    def result(self):
        return sorted(self._result, key=lambda execution: execution.start_timestamp)


class DFSDescendantView(object):
    def __init__(self):
        self._result = []

    def add(self, child):
        self._result.append(child)

    @property
    def result(self):
        return self._result


DESCENDANT_VIEWS = {
    'sorted': AscendingSortedDescendantView,
    'default': DFSDescendantView
}


def get_descendants(actionexecution_id, descendant_depth=-1, result_fmt=None):
    """
    Returns all descendant executions upto the specified descendant_depth for
    the supplied actionexecution_id.
    """
    descendants = DESCENDANT_VIEWS.get(result_fmt, DFSDescendantView)()
    children = ActionExecution.query(parent=actionexecution_id,
                                     **{'order_by': ['start_timestamp']})
    LOG.debug('Found %s children for id %s.', len(children), actionexecution_id)
    current_level = [(child, 1) for child in children]

    while current_level:
        parent, level = current_level.pop(0)
        parent_id = str(parent.id)
        descendants.add(parent)
        if not parent.children:
            continue
        if level != -1 and level == descendant_depth:
            continue
        children = ActionExecution.query(parent=parent_id, **{'order_by': ['start_timestamp']})
        LOG.debug('Found %s children for id %s.', len(children), parent_id)
        # prepend for DFS
        for idx in range(len(children)):
            current_level.insert(idx, (children[idx], level + 1))
    return descendants.result
