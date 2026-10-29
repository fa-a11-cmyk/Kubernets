import json
import logging
import os
import time
from contextlib import asynccontextmanager
import redis
from fastapi import FastAPI, HTTPException, status
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from .database import Base, SessionLocal, engine
from .models import Item
from .schemas import ItemCreate, ItemRead

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("items-api")
redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)
CACHE_KEY = "items:v1"
CACHE_TTL_SECONDS = 30


def initialize_database() -> None:
    # Retry briefly while PostgreSQL starts in local and single-node clusters.
    for attempt in range(1, 31):
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext('microservices-items-api-schema'))")
                )
                Base.metadata.create_all(bind=connection)
            logger.info("database schema ready")
            return
        except SQLAlchemyError:
            if attempt == 30:
                raise
            logger.warning("database not ready (attempt %s/30)", attempt)
            time.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield
    redis_client.close()
    engine.dispose()


app = FastAPI(
    title="Items API",
    version="1.0.0",
    description="Example FastAPI service using PostgreSQL and Redis.",
    lifespan=lifespan,
)
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready", tags=["health"])
@app.get("/health", tags=["health"], include_in_schema=False)
def ready() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        redis_client.ping()
    except (SQLAlchemyError, redis.RedisError) as exc:
        logger.warning("readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="dependencies unavailable") from exc
    return {"status": "ready"}


@app.get("/items", response_model=list[ItemRead], tags=["items"])
def list_items() -> list[ItemRead]:
    try:
        cached = redis_client.get(CACHE_KEY)
        if cached is not None:
            return [ItemRead.model_validate(item) for item in json.loads(cached)]
    except (redis.RedisError, json.JSONDecodeError):
        logger.info("cache read unavailable; using PostgreSQL")

    with SessionLocal() as db:
        items = db.scalars(select(Item).order_by(Item.id)).all()
        result = [ItemRead.model_validate(item) for item in items]

    try:
        redis_client.setex(
            CACHE_KEY,
            CACHE_TTL_SECONDS,
            json.dumps([item.model_dump() for item in result]),
        )
    except redis.RedisError:
        logger.info("cache write unavailable; returning PostgreSQL result")
    return result


@app.post(
    "/items",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
    tags=["items"],
)
def create_item(payload: ItemCreate) -> ItemRead:
    with SessionLocal() as db:
        item = Item(name=payload.name, description=payload.description)
        db.add(item)
        db.commit()
        db.refresh(item)
        result = ItemRead.model_validate(item)

    try:
        redis_client.delete(CACHE_KEY)
    except redis.RedisError:
        logger.info("cache invalidation unavailable; cached result expires shortly")
    return result

