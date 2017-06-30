from __future__ import print_function

from aenum import NamedTuple
from scription import *
import os
from re import findall, search, IGNORECASE

def extract_hg():
    ns = NameSpace()
    ns.names = []
    for k, v in os.environ.items():
        if k.startswith('HG_'):
            name = k[3:].lower()
            ns.names.append(name)
            ns[name] = v
    if 'parent2' not in ns:
        ns['parent2'] = ''
    if 'node' in ns:
        others = Execute(
                "hg log -r %s --template '"
                    "author:{author}\n"
                    "branch:{branches}\n"
                    "date:{date}\n"
                    "description:{desc}\n"
                    "files:{files}\n"
                    "added_files:{file_adds}\n"
                    "removed_files:{file_dels}\n"
                    "revision:{rev}\n"
                    "tags:{tags}\n"
                    "'"
                % ns.node
                ).stdout.strip().split('\n')
        for line in others:
            k, v = line.split(':', 1)
            ns[k] = v
            ns.names.append(k)
        for k in ('files', 'added_files', 'removed_files', 'tags'):
            v = ns[k]
            ns[k] = [t.strip() for t in v.split(',') if t.strip()]
    return ns

def find_issues(*messages):
    found_issues = []
    for msg in messages:
        issue_stanza = search(r'\([^\)]*\)\s*$', msg)
        if issue_stanza:
            issues = [Issue(i.lower()) for i in findall(r'(fal-[ti]\d+|whc-[ti]\d+)', issue_stanza.group(), IGNORECASE)]
            if issues:
                found_issues.extend(issues)
    return found_issues

servers = {
    'fal':  'openerp.sunridgefarms.com',
    }

unabbr = {
    't': 'task',
    'i': 'issue',
    }

class Issue(NamedTuple):
    _order_ = 'company host type id'
    company = 'abbreviation for the company where issue is being tracked'
    host = 'host where issue is being tracked'
    type = 'is issue type a task or issue/bug'
    id   = 'id of issue'
    def __new__(cls, clump):
        company, issue = clump.split('-')
        host = servers.get(company)
        type, id = unabbr[issue[0]], int(issue[1:])
        return super(Issue, cls).__new__(cls, company, host, type, id)
