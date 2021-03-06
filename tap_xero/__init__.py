#!/usr/bin/env python3
import os
import singer
import json
from singer import metrics, utils
from singer.catalog import Catalog
from singer.catalog import Catalog, CatalogEntry, Schema
from . import streams as streams_
from . import credentials
from .http import XeroClient
from .context import Context

CREDENTIALS_KEYS = ["consumer_key",
                    "consumer_secret",
                    "rsa_key"]
REQUIRED_CONFIG_KEYS = ["start_date"] + CREDENTIALS_KEYS

LOGGER = singer.get_logger()

BAD_CREDS_MESSAGE = (
    "Failed to refresh OAuth token using the credentials from the connection. "
    "The token might need to be reauthorized from the integration's properties "
    "or there could be another authentication issue. Please attempt to reauthorize "
    "the integration."
)


class BadCredsException(Exception):
    pass


def get_abs_path(path):
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), path)


def load_schema(tap_stream_id):
    path = "schemas/{}.json".format(tap_stream_id)
    schema = utils.load_json(get_abs_path(path))
    dependencies = schema.pop("tap_schema_dependencies", [])
    refs = {}
    for sub_stream_id in dependencies:
        refs[sub_stream_id] = load_schema(sub_stream_id)
    if refs:
        singer.resolve_schema_references(schema, refs)
    return schema


def ensure_credentials_are_valid(config):
    XeroClient(config).filter("currencies")


def discover(config):
    ensure_credentials_are_valid(config)
    catalog = Catalog([])
    for stream in streams_.all_streams:
        schema = Schema.from_dict(load_schema(stream.tap_stream_id),
                                  inclusion="automatic")
        catalog.streams.append(CatalogEntry(
            stream=stream.tap_stream_id,
            tap_stream_id=stream.tap_stream_id,
            key_properties=stream.pk_fields,
            schema=schema,
        ))
    return catalog


def init_credentials(config):
    if credentials.can_use_s3(config):
        creds = credentials.download_from_s3(config)
        if creds:
            config.update(creds)
        else:
            # no creds means we have to try to use what's in the config
            # to refresh the token
            try:
                config = credentials.refresh(config)
            except Exception as ex:
                raise BadCredsException(BAD_CREDS_MESSAGE) from ex
    return config


def load_and_write_schema(stream):
    singer.write_schema(
        stream.tap_stream_id,
        load_schema(stream.tap_stream_id),
        stream.pk_fields,
    )


def sync(ctx):
    init_credentials(ctx.config)
    currently_syncing = ctx.state.get("currently_syncing")
    start_idx = streams_.all_stream_ids.index(currently_syncing) \
        if currently_syncing else 0
    stream_ids_to_sync = [cs.tap_stream_id for cs in ctx.catalog.streams
                          if cs.is_selected()]
    streams = [s for s in streams_.all_streams[start_idx:]
               if s.tap_stream_id in stream_ids_to_sync]
    for stream in streams:
        ctx.state["currently_syncing"] = stream.tap_stream_id
        ctx.write_state()
        load_and_write_schema(stream)
        stream.sync(ctx)
    ctx.state["currently_syncing"] = None
    ctx.write_state()



def main_impl():
    args = utils.parse_args(REQUIRED_CONFIG_KEYS)
    if args.discover:
        discover(args.config).dump()
        print()
    else:
        catalog = Catalog.from_dict(args.properties) \
            if args.properties else discover(args.config)
        sync(Context(args.config, args.state, catalog))

def main():
    try:
        main_impl()
    except Exception as exc:
        LOGGER.critical(exc)
        raise exc

        
if __name__ == "__main__":
    main()
