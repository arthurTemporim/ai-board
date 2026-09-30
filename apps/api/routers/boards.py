from __future__ import annotations

from fastapi import APIRouter, Response, status

from apps.api.deps import SessionDep
from apps.api.schemas import (
    BoardCreate,
    BoardDetail,
    BoardRead,
    BoardUpdate,
    ColumnCreate,
    ColumnRead,
    ColumnUpdate,
)
from core.services.boards import BoardService

router = APIRouter(tags=["boards"])


@router.get("/boards", response_model=list[BoardRead])
def list_boards(session: SessionDep):
    return BoardService(session).list_boards()


@router.post("/boards", response_model=BoardRead, status_code=status.HTTP_201_CREATED)
def create_board(body: BoardCreate, session: SessionDep):
    return BoardService(session).create_board(body.name, body.description, body.columns)


@router.get("/boards/{board_id}", response_model=BoardDetail, summary="Board with columns and cards")
def get_board(board_id: int, session: SessionDep):
    view = BoardService(session).board_view(board_id)
    return {
        **BoardRead.model_validate(view.board).model_dump(),
        "columns": [
            {**ColumnRead.model_validate(column).model_dump(), "cards": cards}
            for column, cards in view.columns
        ],
    }


@router.patch("/boards/{board_id}", response_model=BoardRead)
def update_board(board_id: int, body: BoardUpdate, session: SessionDep):
    return BoardService(session).update_board(board_id, body.model_dump(exclude_unset=True))


@router.delete("/boards/{board_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_board(board_id: int, session: SessionDep):
    BoardService(session).delete_board(board_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/boards/{board_id}/columns", response_model=list[ColumnRead], tags=["columns"])
def list_columns(board_id: int, session: SessionDep):
    return [c for c, _ in BoardService(session).board_view(board_id).columns]


@router.post(
    "/boards/{board_id}/columns",
    response_model=ColumnRead,
    status_code=status.HTTP_201_CREATED,
    tags=["columns"],
)
def create_column(board_id: int, body: ColumnCreate, session: SessionDep):
    return BoardService(session).add_column(board_id, body.name, body.wip_limit, body.position)


@router.get("/columns/{column_id}", response_model=ColumnRead, tags=["columns"])
def get_column(column_id: int, session: SessionDep):
    return BoardService(session).get_column(column_id)


@router.patch("/columns/{column_id}", response_model=ColumnRead, tags=["columns"])
def update_column(column_id: int, body: ColumnUpdate, session: SessionDep):
    return BoardService(session).update_column(column_id, body.model_dump(exclude_unset=True))


@router.delete("/columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["columns"])
def delete_column(column_id: int, session: SessionDep):
    BoardService(session).delete_column(column_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
