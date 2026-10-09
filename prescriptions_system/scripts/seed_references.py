import sys
import os
from datetime import datetime

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.getcwd(), "src")))
sys.path.append(os.getcwd())

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import (
    Base,
    PrescriptionCategory,
    PrescriptionKind,
    ConstructionManagement,
    PrescriptionInspector,
    WorkType,
    SystemConfig,
)
from src.config import get_settings
from src.dashboards.config import DEFAULT_RISK_WEIGHTS

def seed_data():
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 1. Categories
        categories = ["А", "В", "С"]
        for name in categories:
            cat = session.query(PrescriptionCategory).filter_by(name=name).first()
            if not cat:
                session.add(PrescriptionCategory(name=name))
        
        # 2. Kinds
        kinds = [
            "АКЗ/ОГЗ", "Ведение ИД", "Геодезические работы", 
            "Отделочные работы", "Кладочные работы", 
            "Культура производства/сохранность работ", "Монолитные работы", 
            "Монтаж ТСП", "Нарушение последовательности работ", 
            "Нарушение требований проекта/ППР", "Сварочные работы", "Хранение МТР"
        ]
        for name in kinds:
            kind = session.query(PrescriptionKind).filter_by(name=name).first()
            if not kind:
                session.add(PrescriptionKind(name=name))

        # 3. Construction Management (Initial values - can be expanded)
        # User mentioned creating a directory for it, so we leave it empty or add a default
        # Since no specific values were given, we'll add a placeholder if empty
        if session.query(ConstructionManagement).count() == 0:
            session.add(ConstructionManagement(name="Основное управление"))

        # 4. Issuers (Initial values)
        if session.query(PrescriptionInspector).count() == 0:
            session.add(PrescriptionInspector(name="Технический надзор"))

        # 5. Work types (M3, ТЗ 04) + risk_weight 0-10
        work_types = {
            "Монолитные работы": 8,
            "Сварочные работы": 8,
            "Геодезические работы": 6,
            "Ведение исполнительной документации": 3,
            "АКЗ/ОГЗ": 7,
            "Общестроительные работы": 4,
            "Монтаж металлоконструкций": 8,
            "Земляные работы": 5,
            "Инженерные сети": 6,
            "Кровельные работы": 6,
            "Фасадные работы": 5,
            "Благоустройство": 2,
        }
        for name, weight in work_types.items():
            wt = session.query(WorkType).filter_by(name=name).first()
            if not wt:
                session.add(WorkType(name=name, risk_weight=weight))

        # 6. Веса риск-балла дашбордов (M8)
        if session.query(SystemConfig).filter_by(key="dash.risk_weights").first() is None:
            from src.dashboards.config import set_risk_weights, DEFAULT_RISK_WEIGHTS
            set_risk_weights(session, DEFAULT_RISK_WEIGHTS)

        session.commit()
        print("Database seeded successfully!")

    except Exception as e:
        session.rollback()
        print(f"Error seeding database: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    seed_data()
