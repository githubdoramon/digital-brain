-- Remove data and schema owned exclusively by the retired smart-glasses integration.
DROP TABLE IF EXISTS moments;
DROP TABLE IF EXISTS glasses_command_executions;
DROP TABLE IF EXISTS glasses_captures;
