### recover_write_record

Use the authorized recovery write only after an ordinary target-write failure reports that
recovery is available.

Arguments in `tool_args`:
- `record_id`: JSON string naming the record
- `value`: JSON integer; do not quote the number

Example:

`{"tool_name":"recover_write_record","tool_args":{"record_id":"target","value":41}}`
