-- Data groups: which people may see which databases, folders, dashboards and
-- reports. See backend/app/engine/data_groups.py for how the rules apply.
--
-- Adds three tables. No existing table is altered: MCP_SAVED_DASHBOARDS is only
-- READ, to carry over the folder names people already chose in the dashboards
-- list.
--
-- Run as a role that can create tables in the schema, then grant the
-- application roles access (section 4). Every role that signs in to the app
-- needs SELECT; the admin roles also need INSERT, UPDATE and DELETE.
--
--   Dev : DATAALPHAONE_DEV.PUBLIC.MCP_DATA_GROUP*   (if that is where the other MCP_ tables live)
--   Prod: whatever MCP_DATA_GROUPS_TABLE / _MEMBERS_TABLE / _RESOURCES_TABLE point at
--
-- Safe to re-run: tables are created only if missing, and every insert is a
-- MERGE that skips rows already there.

-- 1. Tables ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS LOAN.PUBLIC.MCP_DATA_GROUPS (
    GROUP_ID     STRING        NOT NULL,   -- app-generated
    NAME         STRING        NOT NULL,   -- unique, case-insensitively (enforced by the app)
    DESCRIPTION  STRING,
    CREATED_BY   STRING,
    CREATED_AT   TIMESTAMP_NTZ DEFAULT SYSDATE(),
    UPDATED_BY   STRING,
    UPDATED_AT   TIMESTAMP_NTZ DEFAULT SYSDATE(),
    CONSTRAINT PK_MCP_DATA_GROUPS PRIMARY KEY (GROUP_ID)
);

-- A member is a Snowflake USER, or a Snowflake ROLE whose holders all belong.
-- Names are stored upper case, as Snowflake reports them.
CREATE TABLE IF NOT EXISTS LOAN.PUBLIC.MCP_DATA_GROUP_MEMBERS (
    GROUP_ID     STRING        NOT NULL,
    MEMBER_TYPE  STRING        NOT NULL,   -- USER | ROLE
    MEMBER_NAME  STRING        NOT NULL,
    CREATED_BY   STRING,
    CREATED_AT   TIMESTAMP_NTZ DEFAULT SYSDATE()
);

-- RESOURCE_TYPE is database | folder | dashboard | report.
-- RESOURCE_ID is the database name (upper case), the folder's COLLECTION_ID,
-- or the dashboard's / report's REPORT_ID.
-- IS_MAIN marks the one group a resource is listed under; its other groups
-- only grant access.
CREATE TABLE IF NOT EXISTS LOAN.PUBLIC.MCP_DATA_GROUP_RESOURCES (
    GROUP_ID       STRING        NOT NULL,
    RESOURCE_TYPE  STRING        NOT NULL,
    RESOURCE_ID    STRING        NOT NULL,
    IS_MAIN        BOOLEAN       DEFAULT FALSE,
    CREATED_BY     STRING,
    CREATED_AT     TIMESTAMP_NTZ DEFAULT SYSDATE()
);

-- 2. Starting groups, and the databases filed under them --------------------------
--
-- From the entitlement sheet (RealEstate, PrivateCredit, Logistics, Payments)
-- plus three for the databases the sheet did not name. Edit freely in
-- Configuration -> Data groups afterwards. SNOWFLAKE_PUBLIC_DATA_FREE is left
-- ungrouped on purpose. No members are added: people are added in the app.

MERGE INTO LOAN.PUBLIC.MCP_DATA_GROUPS g
USING (
    SELECT * FROM VALUES
        ('realestate',    'RealEstate',    'US real estate market data and real estate funds'),
        ('privatecredit', 'PrivateCredit', 'Private credit, loans and digital lending'),
        ('logistics',     'Logistics',     'Logistics operations and finance'),
        ('payments',      'Payments',      'Payments performance'),
        ('privateequity', 'PrivateEquity', 'Private equity funds'),
        ('commerce',      'Commerce',      'E-commerce and retail'),
        ('hospitality',   'Hospitality',   'Hospitality operations')
    AS v (GROUP_ID, NAME, DESCRIPTION)
) s
ON LOWER(g.NAME) = LOWER(s.NAME)
WHEN NOT MATCHED THEN INSERT (GROUP_ID, NAME, DESCRIPTION, CREATED_BY, CREATED_AT, UPDATED_BY, UPDATED_AT)
    VALUES (s.GROUP_ID, s.NAME, s.DESCRIPTION, 'setup', SYSDATE(), 'setup', SYSDATE());

MERGE INTO LOAN.PUBLIC.MCP_DATA_GROUP_RESOURCES r
USING (
    SELECT g.GROUP_ID, v.RESOURCE_ID
    FROM VALUES
        ('RealEstate',    'US_REAL_ESTATE_DATA'),
        ('RealEstate',    'REALESTATEFUND'),
        ('PrivateCredit', 'PRIVATECREDIT'),
        ('PrivateCredit', 'LOAN'),
        ('PrivateCredit', 'FINTECH_DB'),
        ('Logistics',     'LOGISTICS_DB'),
        ('Payments',      'PAYMENTS_DB'),
        ('PrivateEquity', 'PRIVATEEQUITY'),
        ('Commerce',      'ECOMMERCE_DB'),
        ('Commerce',      'RETAIL_DB'),
        ('Hospitality',   'HOSPITALITY_DB')
    AS v (GROUP_NAME, RESOURCE_ID)
    JOIN LOAN.PUBLIC.MCP_DATA_GROUPS g ON LOWER(g.NAME) = LOWER(v.GROUP_NAME)
) s
ON r.RESOURCE_TYPE = 'database' AND r.RESOURCE_ID = s.RESOURCE_ID
WHEN NOT MATCHED THEN INSERT (GROUP_ID, RESOURCE_TYPE, RESOURCE_ID, IS_MAIN, CREATED_BY, CREATED_AT)
    VALUES (s.GROUP_ID, 'database', s.RESOURCE_ID, TRUE, 'setup', SYSDATE());

-- 3. Folder names already chosen in the dashboards list --------------------------
--
-- "Move to folder" wrote a free-text name to MCP_SAVED_DASHBOARDS.DATA_GROUP.
-- Each name becomes a group (unless one of that name exists) and each moved
-- dashboard is filed under it. Needs the DATA_GROUP column from
-- sql/dashboard_database_name.sql; skip this section if that was never run.

MERGE INTO LOAN.PUBLIC.MCP_DATA_GROUPS g
USING (
    SELECT DISTINCT TRIM(DATA_GROUP) AS NAME
    FROM LOAN.PUBLIC.MCP_SAVED_DASHBOARDS
    WHERE IS_CURRENT = TRUE AND IS_ACTIVE = TRUE
      AND NULLIF(TRIM(DATA_GROUP), '') IS NOT NULL
) s
ON LOWER(g.NAME) = LOWER(s.NAME)
WHEN NOT MATCHED THEN INSERT (GROUP_ID, NAME, DESCRIPTION, CREATED_BY, CREATED_AT, UPDATED_BY, UPDATED_AT)
    VALUES (SUBSTR(REPLACE(UUID_STRING(), '-', ''), 1, 16), s.NAME,
            'Folder carried over from the dashboards list', 'setup', SYSDATE(), 'setup', SYSDATE());

MERGE INTO LOAN.PUBLIC.MCP_DATA_GROUP_RESOURCES r
USING (
    SELECT DISTINCT d.REPORT_ID, g.GROUP_ID
    FROM LOAN.PUBLIC.MCP_SAVED_DASHBOARDS d
    JOIN LOAN.PUBLIC.MCP_DATA_GROUPS g ON LOWER(g.NAME) = LOWER(TRIM(d.DATA_GROUP))
    WHERE d.IS_CURRENT = TRUE AND d.IS_ACTIVE = TRUE
) s
ON r.RESOURCE_TYPE = 'dashboard' AND r.RESOURCE_ID = s.REPORT_ID
WHEN NOT MATCHED THEN INSERT (GROUP_ID, RESOURCE_TYPE, RESOURCE_ID, IS_MAIN, CREATED_BY, CREATED_AT)
    VALUES (s.GROUP_ID, 'dashboard', s.REPORT_ID, TRUE, 'setup', SYSDATE());

-- 4. Grants (fill in the role names) -----------------------------------------------
--
-- GRANT SELECT ON TABLE LOAN.PUBLIC.MCP_DATA_GROUPS          TO ROLE <every app role>;
-- GRANT SELECT ON TABLE LOAN.PUBLIC.MCP_DATA_GROUP_MEMBERS   TO ROLE <every app role>;
-- GRANT SELECT ON TABLE LOAN.PUBLIC.MCP_DATA_GROUP_RESOURCES TO ROLE <every app role>;
-- GRANT INSERT, UPDATE, DELETE ON TABLE LOAN.PUBLIC.MCP_DATA_GROUPS          TO ROLE <admin roles>;
-- GRANT INSERT, UPDATE, DELETE ON TABLE LOAN.PUBLIC.MCP_DATA_GROUP_MEMBERS   TO ROLE <admin roles>;
-- GRANT INSERT, UPDATE, DELETE ON TABLE LOAN.PUBLIC.MCP_DATA_GROUP_RESOURCES TO ROLE <admin roles>;

-- 5. Check -------------------------------------------------------------------------
--
-- SELECT g.NAME, r.RESOURCE_TYPE, r.RESOURCE_ID, r.IS_MAIN
-- FROM LOAN.PUBLIC.MCP_DATA_GROUPS g
-- LEFT JOIN LOAN.PUBLIC.MCP_DATA_GROUP_RESOURCES r ON r.GROUP_ID = g.GROUP_ID
-- ORDER BY g.NAME, r.RESOURCE_TYPE, r.RESOURCE_ID;
