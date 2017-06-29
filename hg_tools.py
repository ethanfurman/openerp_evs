from scription import *
import os

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
        ns[k] = [t.strip() for t in v.split(',')]
    return ns

