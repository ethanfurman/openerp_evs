from scription import Execute, echo, error

def update_owner(ui, repo, **kwds):
    '''
    set all files/directories owner to whomever owns the base repo directory
    '''
    url = repo.url()
    if url.startswith('file:'):
        home = url[5:]
        job = Execute('/usr/local/bin/hgupdate set-owner-of %s' % home)
        echo(job.stdout, end='')
        error(job.stderr, end='')
        return job.returncode

def update_permissions(ui, repo, **kwds):
    '''
    add group write permission bit
    '''
    url = repo.url()
    if url.startswith('file:'):
        home = url[5:]
        job = Execute('/usr/local/bin/hgupdate add-group-write-to %s' % home)
        echo(job.stdout, end='')
        error(job.stderr, end='')
        return job.returncode

def install_files(ui, repo, **kwds):
    '''
    install files in .hgupdate_install to /opt/bin
    '''
    url = repo.url()
    if url.startswith('file:'):
        home = url[5:]
        job = Execute('/usr/local/bin/hgupdate install-to-opt-bin %s' % home)
        echo(job.stdout, end='')
        error(job.stderr, end='')
        return job.returncode
