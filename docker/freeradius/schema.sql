-- Schema minimal FreeRADIUS (PostgreSQL). Sumber: FreeRADIUS raddb/mods-config/sql/main/postgresql.
CREATE TABLE IF NOT EXISTS nas (
    id serial PRIMARY KEY,
    nasname varchar(128) NOT NULL,
    shortname varchar(32) NOT NULL DEFAULT '',
    type varchar(30) NOT NULL DEFAULT 'other',
    ports integer,
    secret varchar(60) NOT NULL,
    server varchar(64),
    community varchar(50),
    description varchar(200)
);
CREATE INDEX IF NOT EXISTS nas_nasname ON nas (nasname);

CREATE TABLE IF NOT EXISTS radcheck (
    id serial PRIMARY KEY,
    username varchar(64) NOT NULL DEFAULT '',
    attribute varchar(64) NOT NULL DEFAULT '',
    op varchar(2) NOT NULL DEFAULT '==',
    value varchar(253) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS radcheck_username ON radcheck (username);

CREATE TABLE IF NOT EXISTS radreply (
    id serial PRIMARY KEY,
    username varchar(64) NOT NULL DEFAULT '',
    attribute varchar(64) NOT NULL DEFAULT '',
    op varchar(2) NOT NULL DEFAULT '=',
    value varchar(253) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS radreply_username ON radreply (username);

CREATE TABLE IF NOT EXISTS radusergroup (
    id serial PRIMARY KEY,
    username varchar(64) NOT NULL DEFAULT '',
    groupname varchar(64) NOT NULL DEFAULT '',
    priority integer NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS radusergroup_username ON radusergroup (username);

CREATE TABLE IF NOT EXISTS radgroupcheck (
    id serial PRIMARY KEY,
    groupname varchar(64) NOT NULL DEFAULT '',
    attribute varchar(64) NOT NULL DEFAULT '',
    op varchar(2) NOT NULL DEFAULT '==',
    value varchar(253) NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS radgroupreply (
    id serial PRIMARY KEY,
    groupname varchar(64) NOT NULL DEFAULT '',
    attribute varchar(64) NOT NULL DEFAULT '',
    op varchar(2) NOT NULL DEFAULT '=',
    value varchar(253) NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS radacct (
    radacctid bigserial PRIMARY KEY,
    acctsessionid varchar(64) NOT NULL DEFAULT '',
    acctuniqueid varchar(32) NOT NULL DEFAULT '',
    username varchar(64) NOT NULL DEFAULT '',
    realm varchar(64),
    nasipaddress inet,
    nasportid varchar(32),
    nasporttype varchar(32),
    acctstarttime timestamp with time zone,
    acctupdatetime timestamp with time zone,
    acctstoptime timestamp with time zone,
    acctinterval bigint,
    acctsessiontime bigint,
    acctauthentic varchar(32),
    connectinfo_start varchar(128),
    connectinfo_stop varchar(128),
    acctinputoctets bigint,
    acctoutputoctets bigint,
    calledstationid varchar(50),
    callingstationid varchar(50),
    acctterminatecause varchar(32),
    servicetype varchar(32),
    framedprotocol varchar(32),
    framedipaddress inet,
    framedipv6address inet,
    framedipv6prefix inet,
    framedinterfaceid varchar(44),
    delegatedipv6prefix inet,
    class varchar(64)
);
CREATE UNIQUE INDEX IF NOT EXISTS radacct_acctuniqueid ON radacct (acctuniqueid);

CREATE TABLE IF NOT EXISTS radpostauth (
    id bigserial PRIMARY KEY,
    username varchar(64) NOT NULL,
    pass varchar(64),
    reply varchar(32),
    authdate timestamp with time zone DEFAULT now() NOT NULL
);
