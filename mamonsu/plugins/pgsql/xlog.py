# -*- coding: utf-8 -*-

from mamonsu.plugins.pgsql.plugin import PgsqlPlugin as Plugin
from .pool import Pooler


class Xlog(Plugin):

    def __init__(self, config):
        super(Xlog, self).__init__(config)
        self.TriggerLagMoreThan = self.config.fetch(
            'postgres', 'replication_lag_time', int)
        self.TriggerLagBytesMoreThan = self.config.fetch(
            'postgres', 'replication_lag_bytes', int)

    def run(self, zbx):
        # recovery
        result = Pooler.query('select pg_is_in_recovery()')
        if str(result[0][0]) == 't' or str(result[0][0]) == 'True':
            lag = Pooler.query("""
                select extract(epoch from now()
                    - pg_last_xact_replay_timestamp())""")
            if lag[0][0] is not None:
                zbx.send('pgsql.replication_lag[sec]', float(lag[0][0]))
        else:
            # xlog location
            result = Pooler.query("""
                select pg_xlog_location_diff
                    (pg_current_xlog_location(),'0/00000000')""")
            zbx.send(
                'pgsql.wal.write[]', float(result[0][0]), self.DELTA_SPEED)
        # discover sync standby servers and lag in bytes
        standbys = []
        result = Pooler.query("""
            select application_name,
                client_addr,
                pg_xlog_location_diff(pg_stat_replication.sent_location,
                    pg_stat_replication.replay_location) as byte_lag
            from pg_stat_replication;""")
        for info in result:
            standbys.append({'{#APPLICATION}': info[0], '{#HOST}': info[1]})
            zbx.send('psql.replication_lag[byte, %s, %s]' % (info[0], info[1]), info[2])
        zbx.send('pgsql.sync_standby.discovery[]', zbx.json({'data': standbys}))

    def items(self, template):
        return template.item({
            'name': 'PostgreSQL: streaming replication lag in seconds',
            'key': 'pgsql.replication_lag[sec]'
        }) + template.item({
            'name': 'PostgreSQL: wal write speed',
            'key': 'pgsql.wal.write[]',
            'units': Plugin.UNITS.bytes
        })

    def graphs(self, template):
        result = template.graph({
            'name': 'PostgreSQL write-ahead log generation speed',
            'items': [
                {'color': 'CC0000',
                    'key': 'pgsql.wal.write[]'}]})
        result += template.graph({
            'name': 'PostgreSQL replication lag in second',
            'items': [
                {'color': 'CC0000',
                    'key': 'pgsql.replication_lag[sec]'}]})
        return result

    def triggers(self, template):
        return template.trigger({
            'name': 'PostgreSQL streaming lag to high '
            'on {HOSTNAME} (value={ITEM.LASTVALUE})',
            'expression': '{#TEMPLATE:pgsql.replication_lag[sec].last'
            '()}&gt;' + str(self.TriggerLagMoreThan)
        })

    def discovery_rules(self, template):
        rule = {
            'name': 'Synchronous standby server discovery',
            'key': 'pgsql.sync_standby.discovery[]',
            'filter': '{#APPLICATION}:.*'
        }
        items = [
            {'key': 'psql.replication_lag[byte, {#APPLICATION}, {#HOST}]',
                'name': 'Replication lag for app {#APPLICATION} on {#HOST} in bytes',
                'units': Plugin.UNITS.bytes,
                'value_type': Plugin.VALUE_TYPE.numeric_unsigned,
                'delay': self.Interval}
        ]
        graphs = [
            {
                'name': '{#APPLICATION}: {#HOST} replication lag',
                'items': [
                    {'color': '00CC00',
                        'key': 'psql.replication_lag[byte, {#APPLICATION}, {#HOST}]'}
                ]
            }
        ]
        triggers = [{
            'name': '{#APPLICATION}: {#HOST} lagging',
            'expression': '{#TEMPLATE:psql.'
            'replication_lag[byte, {#APPLICATION},'
            ' {#HOST}].last()}&gt;' + str(self.TriggerLagBytesMoreThan)
        }]

        return template.discovery_rule(
            rule=rule, items=items, graphs=graphs, triggers=triggers)
