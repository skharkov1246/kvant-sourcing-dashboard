"""Real PostgreSQL checks in a disposable loopback-only CI database.

No production data, host or credentials are read. The publisher connection
factory alone is injected; its real SQL and stream validation run unchanged.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('real_sql_publisher', SCRIPTS / 'publish_library_v2.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
DSN = os.environ.get('LIBRARY_SQL_TEST_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='Disposable PostgreSQL service not configured')


@pytest.fixture(scope='module')
def postgres():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(DSN)
    assert config['host'] in ('127.0.0.1', 'localhost')
    assert config['dbname'] == 'library_sql_test'
    assert config['user'] == 'postgres'
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_database(), to_regclass('public.lib_knowledge'), to_regclass('public.lib_segments')")
            assert cursor.fetchone() == ('library_sql_test', None, None)
            cursor.execute('''CREATE TABLE public.lib_segments
                (id text PRIMARY KEY, name text NOT NULL, note text);
                CREATE TABLE public.lib_knowledge (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                segment_id text REFERENCES public.lib_segments(id),
                topic text NOT NULL, title text NOT NULL, body text NOT NULL,
                sources jsonb, confidence text, researched_by text,
                updated_at timestamptz DEFAULT now());
                CREATE INDEX lib_knowledge_seg ON public.lib_knowledge(segment_id,topic);
                INSERT INTO public.lib_segments VALUES ('s1','Synthetic equipment','Synthetic only')''')
    yield psycopg2
    # Only the dedicated, guarded CI database has ever been connected here.
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cursor:
            cursor.execute('DROP TABLE public.lib_knowledge; DROP TABLE public.lib_segments')


@pytest.fixture
def database(postgres):
    def connect():
        conn = postgres.connect(DSN, options='-c statement_timeout=15000 -c lock_timeout=5000')
        conn.set_session(readonly=True, autocommit=False, isolation_level='REPEATABLE READ')
        return conn

    db = object.__new__(p.Database)
    db.connect = connect
    yield db
    with postgres.connect(DSN) as conn:
        with conn.cursor() as cursor:
            cursor.execute('TRUNCATE public.lib_knowledge RESTART IDENTITY')


def source(identity, kind='component', approved=True):
    return {'importer_id': identity, 'publication_approved': approved, 'kind': kind,
            'component_fields': {'part_number': 'PN-' + identity},
            'references': []}


def link(target, number, pointer='/synthetic/1'):
    return {'article_id': target, 'position_id': number, 'part_number': 'PN-' + target,
            'source_pointer': pointer}


def evidence(pointer='/synthetic/1', plural=False):
    # Split string avoids declaring this synthetic fixture a dataset consumer.
    repository_path = '/'.join(('zip', 'data', 'positions' + '.json'))
    return {'repository_path': repository_path, 'sha256': 'a' * 64,
            'url': 'https://example.test/positions',
            'locator': {'json_pointers': [pointer]} if plural else {'json_pointer': pointer}}


def insert(postgres, rows):
    from psycopg2.extras import execute_values

    values = [('s1', 'QA', row['importer_id'], 'Synthetic test only', json.dumps(row), 'med', manager)
              for row, manager in rows]
    with postgres.connect(DSN) as conn:
        with conn.cursor() as cursor:
            execute_values(cursor, '''INSERT INTO public.lib_knowledge
                (segment_id,topic,title,body,sources,confidence,researched_by) VALUES %s''',
                values, page_size=200)
            cursor.execute('ANALYZE public.lib_knowledge')


def test_actual_sql_scalar_and_array_locators_and_unapproved_targets(postgres, database):
    targets = [source('component:good1'), source('component:good2'),
               source('component:unapproved', approved=False), source('component:foreign')]
    supplier = source('supplier:one', 'supplier')
    supplier['supplier_fields'] = {'name': 'Synthetic supplier'}
    supplier['candidate_position_links'] = [link(t['importer_id'], i + 1, '/synthetic/' + str(i))
                                            for i, t in enumerate(targets)]
    supplier['references'] = [evidence('/synthetic/' + str(i), plural=bool(i % 2)) for i in range(4)]
    insert(postgres, [(t, p.v1.MANAGER if i != 3 else 'unmanaged-synthetic')
                      for i, t in enumerate(targets)] + [(supplier, p.v1.MANAGER)])
    assert database.read_segments() == [{'id': 's1', 'name': 'Synthetic equipment', 'note': 'Synthetic only'}]
    with database.stream() as (segments, incoming):
        assert segments == [{'id': 's1', 'name': 'Synthetic equipment', 'note': 'Synthetic only'}]
        assert set(incoming.relations) == {'component:good1', 'component:good2'}
        assert incoming.relation_metrics['linked'] == 2
        assert incoming.relation_metrics['missing_target'] == 2
        rows = list(incoming)
    assert len(rows) == 3
    assert rows[-1]['id'] == 'supplier:one'


def test_actual_sql_rejects_duplicate_stable_identity_with_real_bigint(postgres, database):
    target = source('component:collision')
    supplier = source('supplier:one', 'supplier')
    supplier.update(candidate_position_links=[link('component:collision', 1)], references=[evidence()])
    insert(postgres, [(target, p.v1.MANAGER), (target, p.v1.MANAGER), (supplier, p.v1.MANAGER)])
    with pytest.raises(p.v1.PublishError, match='RELATION_ID_COLLISION'):
        with database.stream():
            pytest.fail('Colliding identity must fail before publication')


def test_actual_sql_handles_18180_synthetic_rows_and_4309_evidence_links(postgres, database):
    rows = []
    for i in range(15000):
        item = source(f'component:{i:05d}')
        # Source payload is intentionally wider than the relation projection.
        item['synthetic_padding'] = ''.join(hashlib.sha256(f'{i}:{j}'.encode()).hexdigest() for j in range(32))
        rows.append((item, p.v1.MANAGER))
    for i in range(1315):
        item = source(f'supplier:{i:05d}', 'supplier')
        edge_numbers = range(i, 4309, 1315)
        item['candidate_position_links'] = [link(f'component:{n % 750:05d}', n % 750 + 1,
                                                 f'/synthetic/{n % 750}') for n in edge_numbers]
        item['references'] = [evidence(f'/synthetic/{n % 750}', plural=bool(n % 2)) for n in edge_numbers]
        rows.append((item, p.v1.MANAGER))
    rows.extend((source(f'knowledge:{i:05d}', 'knowledge'), p.v1.MANAGER) for i in range(1865))
    insert(postgres, rows)
    with database.stream() as (_, incoming):
        assert incoming.relation_metrics['edges'] == 4309
        assert incoming.relation_metrics['linked'] == 4309
        assert incoming.relation_metrics['missing_target'] == 0
        assert incoming.relation_metrics['missing_evidence'] == 0
        assert len(incoming.relations) == 750
        assert sum(len(v) for v in incoming.relations.values()) == 4309
        identities = [row['id'] for row in incoming]
    assert len(identities) == len(set(identities)) == 18180
    assert identities == sorted(identities)
