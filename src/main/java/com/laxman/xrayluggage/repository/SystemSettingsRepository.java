package com.laxman.xrayluggage.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import com.laxman.xrayluggage.model.SystemSettings;

// Replaces: SQLAlchemy session queries for SystemSettings in database.py
// JpaRepository gives free: save(), findById(), findAll(), etc.
@Repository
public interface SystemSettingsRepository extends JpaRepository<SystemSettings, Long> {
    // No extra methods needed — findAll().get(0) is enough for single settings row
}

