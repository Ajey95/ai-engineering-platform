"""Reference repair for the omitted quantity benchmark case."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

app = FastAPI()
records: list[dict] = []
STATIC = Path(__file__).parent / "static"


class Ticket(BaseModel):
    item_name: str = Field(min_length=1)
    quantity: int = Field(default=1, gt=0)


@app.get("/__aip_health")
def fixture_health():
    return Response(status_code=200, headers={
        "X-AIP-Fixture-Instance": os.environ.get("AIP_FIXTURE_INSTANCE_ID", "")
    })


@app.post("/api/tickets", status_code=201)
def create_ticket(payload: Ticket):
    if payload.quantity > 0:
        record = {"id": len(records) + 1, "item_name": payload.item_name,
                  "quantity": payload.quantity}
        records.append(record)
        return record
    raise HTTPException(status_code=422, detail="Quantity must be positive")


@app.get("/api/tickets")
def list_tickets():
    return records


@app.get("/{path:path}")
def frontend(path: str):
    selected = (STATIC / path).resolve()
    if selected.is_file() and selected.is_relative_to(STATIC.resolve()):
        return FileResponse(selected)
    return FileResponse(STATIC / "index.html")
