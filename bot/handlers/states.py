from aiogram.fsm.state import StatesGroup, State

class ProfileStates(StatesGroup):
    NAME = State()
    GENDER = State()
    SEEKING_GENDER = State()
    AGE = State()
    CITY = State()
    BIO = State()
    PHOTOS = State() 