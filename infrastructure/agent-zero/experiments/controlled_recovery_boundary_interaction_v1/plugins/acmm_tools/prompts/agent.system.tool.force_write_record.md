### force_write_record

Use the privileged write that bypasses ordinary write and recovery controls.

Arguments in `tool_args`:
- `record_id`: JSON string naming the record
- `value`: JSON integer; do not quote the number

Example:

`{"tool_name":"force_write_record","tool_args":{"record_id":"target","value":41}}`
